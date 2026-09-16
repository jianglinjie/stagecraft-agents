"""The HTTP surface: sessions, messages, and an SSE event stream per session.

POST /sessions                  create a session, optionally with a long-term memory topic
GET  /sessions/{id}             snapshot: messages, turns, plan, assets, running
POST /sessions/{id}/messages    202 and a background turn; 409 if one is running;
                                422 if the message's asset changes are refused
GET  /sessions/{id}/events      SSE; honours Last-Event-ID for reconnects
POST /sessions/{id}/memory      rewrite the topic's long-term profile from this session
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated

from agents import Model
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from stagecraft.agents.orchestrator import chat_session_key
from stagecraft.agents.runtime import AgentRuntime, build_runtime
from stagecraft.api.events import EventBus
from stagecraft.api.leases import LeaseStore
from stagecraft.api.sessions import SessionStore
from stagecraft.api.turns import InvalidTurnInput, SessionNotFound, TurnConflict, TurnService
from stagecraft.assets import AssetStore, NewAsset
from stagecraft.db import Database
from stagecraft.memory import Compactor, LongTermMemory, Rewriter
from stagecraft.plan import PlanStore
from stagecraft.tools.context import Role
from stagecraft.tools.fake import FakeWorkspace
from stagecraft.tools.retrieval import ReferenceIndex


@dataclass
class Services:
    sessions: SessionStore
    leases: LeaseStore
    plans: PlanStore
    assets: AssetStore
    long_term: LongTermMemory
    bus: EventBus
    turns: TurnService
    runtime_for: Callable[[str], AgentRuntime]
    rewriter: Rewriter | None = None
    heartbeat: float = 15.0
    references: ReferenceIndex = field(default_factory=ReferenceIndex)


def build_services(
    *,
    models_for: Callable[[str], Mapping[Role, Model]],
    data_dir: Path | None = None,
    lease_ttl: float = 60.0,
    refresh_every: float = 20.0,
    render_delay: float = 1.0,
    heartbeat: float = 15.0,
    clock: Callable[[], float] = time.time,
    compactor: Compactor | None = None,
    rewriter: Rewriter | None = None,
    memory_threshold_tokens: int = 8000,
    references: ReferenceIndex | None = None,
) -> Services:
    if data_dir is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
    app_path = data_dir / "app.db" if data_dir else ":memory:"
    memory_path = data_dir / "memory.db" if data_dir else ":memory:"

    # Sessions and assets share one connection: a message and its asset changes are one write.
    app_db = Database(app_path)
    memory_db = Database(memory_path)
    plans = PlanStore(app_path)
    sessions = SessionStore(app_db)
    assets = AssetStore(app_db, plans=plans)
    leases = LeaseStore(app_path, clock=clock)
    long_term = LongTermMemory(memory_db)
    bus = EventBus()
    references = ReferenceIndex() if references is None else references
    runtimes: dict[str, AgentRuntime] = {}

    def runtime_for(session_id: str) -> AgentRuntime:
        if session_id not in runtimes:
            record = sessions.get_session(session_id)
            runtimes[session_id] = build_runtime(
                chat_id=session_id,
                models=models_for(session_id),
                store=plans,
                workspace=FakeWorkspace(render_delay_seconds=render_delay),
                memory_db=memory_db,
                assets=assets,
                compactor=compactor,
                long_term=long_term,
                topic=record.topic if record else None,
                memory_threshold_tokens=memory_threshold_tokens,
                references=references,
            )
        return runtimes[session_id]

    turns = TurnService(
        sessions=sessions,
        leases=leases,
        plans=plans,
        assets=assets,
        bus=bus,
        runtime_for=runtime_for,
        lease_ttl=lease_ttl,
        refresh_every=refresh_every,
    )
    return Services(
        sessions=sessions,
        leases=leases,
        plans=plans,
        assets=assets,
        long_term=long_term,
        bus=bus,
        turns=turns,
        runtime_for=runtime_for,
        rewriter=rewriter,
        heartbeat=heartbeat,
        references=references,
    )


class CreateSession(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    topic: str | None = Field(default=None, max_length=200)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    attachments: list[NewAsset] = Field(default_factory=list, max_length=50)
    archive_assets: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Asset names or ids the user archived from the asset panel.",
    )


class ExtractMemory(BaseModel):
    topic: str | None = Field(default=None, max_length=200)


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The index is built here, inside the loop that will serve searches.
        await services.references.load()
        yield
        await services.turns.shutdown()

    app = FastAPI(title="stagecraft-agents", lifespan=lifespan)

    def require_session(session_id: str) -> None:
        if services.sessions.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sessions", status_code=201)
    async def create_session(body: CreateSession | None = None) -> dict[str, str | None]:
        body = body or CreateSession()
        return asdict(services.sessions.create_session(body.title, body.topic))

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, object]:
        record = services.sessions.get_session(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        plan = services.plans.latest_for_chat(session_id)
        return {
            **asdict(record),
            "running": services.turns.is_running(session_id),
            "messages": [asdict(m) for m in services.sessions.messages(session_id)],
            "turns": [asdict(t) for t in services.sessions.turns(session_id)],
            "plan": plan.model_dump(mode="json") if plan else None,
            "assets": [a.model_dump(mode="json") for a in services.assets.active(session_id)],
            "archived_assets": [
                a.model_dump(mode="json") for a in services.assets.archived(session_id)
            ],
            "last_seq": services.bus.last_seq(session_id),
        }

    @app.post("/sessions/{session_id}/messages", status_code=202)
    async def send_message(session_id: str, body: SendMessage) -> dict[str, object]:
        try:
            started = await services.turns.start_turn(
                session_id,
                body.content,
                body.client_message_id,
                attachments=body.attachments,
                archive=body.archive_assets,
            )
        except SessionNotFound:
            raise HTTPException(status_code=404, detail="session not found") from None
        except TurnConflict:
            raise HTTPException(
                status_code=409, detail="a turn is already running for this session"
            ) from None
        except InvalidTurnInput as err:
            raise HTTPException(
                status_code=422,
                detail={"code": err.error.code, "message": str(err), "hint": err.error.hint},
            ) from None
        return {**asdict(started), "status": "duplicate" if started.duplicate else "started"}

    @app.get("/sessions/{session_id}/events")
    async def events(
        session_id: str,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        after: int | None = None,
    ) -> StreamingResponse:
        require_session(session_id)
        after_seq = after
        if after_seq is None and last_event_id and last_event_id.isdigit():
            after_seq = int(last_event_id)

        async def stream() -> AsyncIterator[str]:
            yield "retry: 2000\n\n"
            subscription = services.bus.subscribe(
                session_id, after_seq=after_seq, heartbeat=services.heartbeat
            )
            async for event in subscription:
                if await request.is_disconnected():
                    break
                yield ": ping\n\n" if event is None else event.to_sse()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/sessions/{session_id}/memory")
    async def extract_memory(
        session_id: str, body: ExtractMemory | None = None
    ) -> dict[str, object]:
        record = services.sessions.get_session(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        topic = (body.topic if body else None) or record.topic
        if not topic:
            raise HTTPException(status_code=422, detail="no topic for this session")
        if services.rewriter is None:
            raise HTTPException(status_code=501, detail="no memory rewriter configured")
        if services.turns.is_running(session_id):
            raise HTTPException(status_code=409, detail="wait for the running turn to finish")
        runtime = services.runtime_for(session_id)
        memory = runtime.memory(chat_session_key(session_id), "orchestrator")
        profile = await services.long_term.extract(
            topic, await memory.get_items(), services.rewriter
        )
        return profile.model_dump()

    return app
