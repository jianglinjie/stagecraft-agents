"""The HTTP surface: sessions, messages, and an SSE event stream per session.

POST /sessions                      create a session
GET  /sessions/{id}                 snapshot: messages, turns, plan, running
POST /sessions/{id}/messages        202 and a background turn; 409 if one is running
GET  /sessions/{id}/events          SSE; honours Last-Event-ID for reconnects
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

from agents import Model
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from stagecraft.agents.runtime import AgentRuntime, build_runtime
from stagecraft.api.events import EventBus
from stagecraft.api.leases import LeaseStore
from stagecraft.api.sessions import SessionStore
from stagecraft.api.turns import SessionNotFound, TurnConflict, TurnService
from stagecraft.plan import PlanStore
from stagecraft.tools.context import Role
from stagecraft.tools.fake import FakeWorkspace


@dataclass
class Services:
    sessions: SessionStore
    leases: LeaseStore
    plans: PlanStore
    bus: EventBus
    turns: TurnService
    heartbeat: float = 15.0


def build_services(
    *,
    models_for: Callable[[str], Mapping[Role, Model]],
    data_dir: Path | None = None,
    lease_ttl: float = 60.0,
    refresh_every: float = 20.0,
    render_delay: float = 1.0,
    heartbeat: float = 15.0,
    clock: Callable[[], float] = time.time,
) -> Services:
    if data_dir is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
    app_db = data_dir / "app.db" if data_dir else ":memory:"
    agents_db = data_dir / "agents.db" if data_dir else None

    sessions = SessionStore(app_db)
    leases = LeaseStore(app_db, clock=clock)
    plans = PlanStore(app_db)
    bus = EventBus()
    runtimes: dict[str, AgentRuntime] = {}

    def runtime_for(session_id: str) -> AgentRuntime:
        if session_id not in runtimes:
            runtimes[session_id] = build_runtime(
                chat_id=session_id,
                models=models_for(session_id),
                store=plans,
                workspace=FakeWorkspace(render_delay_seconds=render_delay),
                session_db=agents_db,
            )
        return runtimes[session_id]

    turns = TurnService(
        sessions=sessions,
        leases=leases,
        plans=plans,
        bus=bus,
        runtime_for=runtime_for,
        lease_ttl=lease_ttl,
        refresh_every=refresh_every,
    )
    return Services(sessions, leases, plans, bus, turns, heartbeat=heartbeat)


class CreateSession(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
        record = services.sessions.create_session(body.title if body else None)
        return asdict(record)

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
            "last_seq": services.bus.last_seq(session_id),
        }

    @app.post("/sessions/{session_id}/messages", status_code=202)
    async def send_message(session_id: str, body: SendMessage) -> dict[str, object]:
        try:
            started = await services.turns.start_turn(
                session_id, body.content, body.client_message_id
            )
        except SessionNotFound:
            raise HTTPException(status_code=404, detail="session not found") from None
        except TurnConflict:
            raise HTTPException(
                status_code=409, detail="a turn is already running for this session"
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

    return app
