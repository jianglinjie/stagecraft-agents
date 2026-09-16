"""The product as an MCP server: an IDE or another agent drives sessions through tools.

Four tools, and they are the product's *user-facing* operations, the same ones the
HTTP API offers, not the agents' internal tools:

* ``create_session``
* ``send_message``: starts a turn and, by default, waits for it, forwarding each tool
  call as an MCP progress notification (the SSE stream, reshaped for a request/response
  protocol)
* ``get_plan``
* ``get_stage_detail``

That is the boundary. An MCP client is a user of the product, not a sub-agent. It
cannot move a stage, write a contract or call a content tool directly; it sends
messages, so the confirmation gate, the leases and idempotency apply to it exactly as
they apply to the web UI.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from stagecraft.api.app import Services
from stagecraft.api.turns import InvalidTurnInput, SessionNotFound, TurnConflict
from stagecraft.plan import TERMINAL, PlanNotFound, next_action
from stagecraft.tools.plan import StageDetail, stage_detail

INSTRUCTIONS = """\
stagecraft runs a staged content pipeline (brief, outline, draft, render) behind a chat.
Create a session, then send messages as the user would. Work that costs money only starts
after an explicit approval message, so relay the plan to your user and send their decision.
Use get_plan to see where things stand instead of parsing replies."""


class SessionCreated(BaseModel):
    session_id: str
    title: str | None
    topic: str | None


class TurnOutcome(BaseModel):
    session_id: str
    turn_id: str
    status: Literal["started", "duplicate", "completed", "failed", "timeout"]
    output: str | None = None
    error: str | None = None
    interrupted: bool = False
    tool_calls: list[str] = []
    plan_summary: str | None = None
    next_action: str | None = None


class PlanView(BaseModel):
    session_id: str
    running: bool
    summary: str | None
    next_stage_id: str | None
    next_action: str | None
    plan: dict[str, Any] | None


def build_mcp_server(services: Services, *, name: str = "stagecraft") -> MCPServer:
    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        await services.references.load()
        yield
        await services.turns.shutdown()

    server = MCPServer(name, instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool()
    def create_session(title: str | None = None, topic: str | None = None) -> SessionCreated:
        """Create a chat session. topic names the long-term memory profile to use."""
        record = services.sessions.create_session(title, topic)
        return SessionCreated(session_id=record.id, title=record.title, topic=record.topic)

    @server.tool()
    async def send_message(
        session_id: str,
        content: str,
        ctx: Context,
        client_message_id: str | None = None,
        wait: bool = True,
        timeout_seconds: float = 300,
    ) -> TurnOutcome:
        """Send a user message. Waits for the turn and reports each tool call as progress.

        Pass client_message_id so a retry after a lost response does not start a second turn.
        """
        after = services.bus.last_seq(session_id)
        try:
            started = await services.turns.start_turn(session_id, content, client_message_id)
        except SessionNotFound:
            raise ToolError(f"no session {session_id}; call create_session first") from None
        except TurnConflict:
            raise ToolError(
                "a turn is already running in this session; wait for it, then send again"
            ) from None
        except InvalidTurnInput as err:
            raise ToolError(str(err)) from None

        outcome = TurnOutcome(
            session_id=session_id,
            turn_id=started.turn_id,
            status="duplicate" if started.duplicate else "started",
        )
        if started.duplicate or not wait:
            return _with_plan(services, outcome)

        tool_calls: list[str] = []

        async def follow() -> TurnOutcome:
            async for event in services.bus.subscribe(session_id, after_seq=after):
                if event is None or event.data.get("turn_id") != started.turn_id:
                    continue
                if event.type == "item_started" and event.data.get("kind") == "tool_call":
                    tool_calls.append(str(event.data.get("name")))
                    await ctx.report_progress(len(tool_calls), message=f"calling {tool_calls[-1]}")
                elif event.type == "turn_completed":
                    return outcome.model_copy(
                        update={
                            "status": "completed",
                            "output": event.data.get("output"),
                            "interrupted": bool(event.data.get("interrupted")),
                        }
                    )
                elif event.type == "turn_failed":
                    return outcome.model_copy(
                        update={"status": "failed", "error": event.data.get("message")}
                    )
            return outcome

        try:
            final = await asyncio.wait_for(follow(), timeout_seconds)
        except TimeoutError:
            final = outcome.model_copy(update={"status": "timeout"})
        return _with_plan(services, final.model_copy(update={"tool_calls": tool_calls}))

    @server.tool()
    def get_plan(session_id: str) -> PlanView:
        """Where the session stands: plan summary, the next stage and what should happen next."""
        _require_session(services, session_id)
        plan = services.plans.latest_for_chat(session_id)
        stage = next((s for s in plan.ordered() if s.state not in TERMINAL), None) if plan else None
        return PlanView(
            session_id=session_id,
            running=services.turns.is_running(session_id),
            summary=plan.summary() if plan else None,
            next_stage_id=stage.id if stage else None,
            next_action=next_action(stage) if stage else None,
            plan=plan.model_dump(mode="json") if plan else None,
        )

    @server.tool()
    def get_stage_detail(session_id: str, stage_id: str) -> StageDetail:
        """One stage: contract, resolved upstream refs, produced refs, pending work items."""
        _require_session(services, session_id)
        plan = services.plans.latest_for_chat(session_id)
        if plan is None:
            raise ToolError(f"session {session_id} has no plan yet")
        try:
            plan, stage = services.plans.get_stage(plan.id, stage_id)
        except PlanNotFound as err:
            raise ToolError(f"{err} {err.hint or ''}".strip()) from None
        return stage_detail(plan, stage)

    return server


def _require_session(services: Services, session_id: str) -> None:
    if services.sessions.get_session(session_id) is None:
        raise ToolError(f"no session {session_id}; call create_session first")


def _with_plan(services: Services, outcome: TurnOutcome) -> TurnOutcome:
    plan = services.plans.latest_for_chat(outcome.session_id)
    if plan is None:
        return outcome
    stage = next((s for s in plan.ordered() if s.state not in TERMINAL), None)
    return outcome.model_copy(
        update={
            "plan_summary": plan.summary(),
            "next_action": next_action(stage) if stage else None,
        }
    )
