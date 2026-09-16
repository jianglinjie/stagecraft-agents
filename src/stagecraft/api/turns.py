"""TurnService: persist, lease, run in the background, stream events, clean up.

The request path does only the part that must succeed before answering::

    idempotency check -> lease -> message + turn row -> 202

Everything slow happens after the response, in a task that owns the lease::

    turn_started, state -> streamed run -> item_* events -> turn_completed | turn_failed
    finally: stop refreshing, release the lease, final state

The lease is refreshed on a timer while the run is alive. If a refresh is refused,
another process has taken the session over, so this run is cancelled rather than
allowed to keep writing next to the new owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agents import ItemHelpers, RawResponsesStreamEvent, RunItemStreamEvent

from stagecraft.agents.orchestrator import stream_orchestrator_turn
from stagecraft.agents.runtime import AgentRuntime
from stagecraft.api.events import EventBus
from stagecraft.api.leases import Lease, LeaseHeld, LeaseStore
from stagecraft.api.sessions import (
    DuplicateClientMessage,
    MessageRecord,
    SessionStore,
    TurnRecord,
)
from stagecraft.plan import PlanStore

log = logging.getLogger(__name__)

STATE_TOOL_PREFIXES = ("plan_", "dispatch_")


class SessionNotFound(Exception):
    pass


class TurnConflict(Exception):
    """A turn is already running for this session."""


@dataclass(frozen=True)
class TurnStarted:
    session_id: str
    message_id: str
    turn_id: str
    duplicate: bool


@dataclass
class _TurnState:
    turn_id: str
    tool_names: dict[str, str] = field(default_factory=dict)
    open_messages: set[str] = field(default_factory=set)
    interrupted: bool = False
    lease_lost: bool = False


class TurnService:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        leases: LeaseStore,
        plans: PlanStore,
        bus: EventBus,
        runtime_for: Callable[[str], AgentRuntime],
        lease_ttl: float = 60.0,
        refresh_every: float = 20.0,
    ) -> None:
        self.sessions = sessions
        self.leases = leases
        self.plans = plans
        self.bus = bus
        self.runtime_for = runtime_for
        self.lease_ttl = lease_ttl
        self.refresh_every = refresh_every
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # -- request path ------------------------------------------------------------

    async def start_turn(
        self, session_id: str, content: str, client_message_id: str | None = None
    ) -> TurnStarted:
        if self.sessions.get_session(session_id) is None:
            raise SessionNotFound(session_id)

        # A resend of a stored message starts nothing: its turn is running or done.
        if client_message_id and (
            stored := self.sessions.message_by_client_id(session_id, client_message_id)
        ):
            return _duplicate(stored)

        try:
            lease = self.leases.acquire(session_id, self.lease_ttl)
        except LeaseHeld:
            # The same resend may race its own original, which now holds the lease.
            if client_message_id and (
                stored := self.sessions.message_by_client_id(session_id, client_message_id)
            ):
                return _duplicate(stored)
            raise TurnConflict(session_id) from None

        try:
            message, turn = self.sessions.record_user_message(
                session_id, content, client_message_id
            )
        except DuplicateClientMessage as dup:
            self.leases.release(lease)
            return _duplicate(dup.existing)
        except BaseException:
            self.leases.release(lease)
            raise

        task = asyncio.create_task(self._run(message, turn, lease), name=f"turn:{turn.id}")
        self._tasks[turn.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(turn.id, None))
        return TurnStarted(session_id, message.id, turn.id, duplicate=False)

    def is_running(self, session_id: str) -> bool:
        return self.leases.holder(session_id) is not None

    async def wait_idle(self, timeout: float = 10.0) -> None:
        """Test and shutdown helper: wait for every background turn to finish."""
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        with contextlib.suppress(Exception):
            await self.wait_idle(timeout=5)

    # -- background ----------------------------------------------------------------

    async def _run(self, message: MessageRecord, turn: TurnRecord, lease: Lease) -> None:
        session_id = message.session_id
        state = _TurnState(turn_id=turn.id)
        run_task = asyncio.current_task()
        assert run_task is not None
        refresher = asyncio.create_task(self._keep_lease(lease, state, run_task))

        self._publish(session_id, "turn_started", {"turn_id": turn.id, "message_id": message.id})
        self._publish_state(session_id, running=True)
        try:
            runtime = self.runtime_for(session_id)
            streamed = stream_orchestrator_turn(
                runtime, message.content, turn_id=turn.id, message_id=message.id
            )
            async for event in streamed.stream_events():
                self._translate(session_id, state, event)
            output = str(streamed.final_output)
            self.sessions.add_assistant_message(session_id, turn.id, output)
            self.sessions.finish_turn(turn.id, "completed", output=output)
            self._publish(
                session_id,
                "turn_completed",
                {"turn_id": turn.id, "output": output, "interrupted": state.interrupted},
            )
        except asyncio.CancelledError:
            reason = "lease_lost" if state.lease_lost else "cancelled"
            self.sessions.finish_turn(turn.id, "failed", error=reason)
            self._publish(
                session_id,
                "turn_failed",
                {"turn_id": turn.id, "code": reason, "message": f"turn stopped: {reason}"},
            )
            if not state.lease_lost:
                raise
        except Exception as err:  # noqa: BLE001 - every failure must reach the client
            log.exception("turn %s failed", turn.id)
            self.sessions.finish_turn(turn.id, "failed", error=str(err))
            self._publish(
                session_id,
                "turn_failed",
                {"turn_id": turn.id, "code": "agent_error", "message": str(err)},
            )
        finally:
            refresher.cancel()
            if not state.lease_lost:
                self.leases.release(lease)
            self._publish_state(session_id, running=False)

    async def _keep_lease(
        self, lease: Lease, state: _TurnState, run_task: asyncio.Task[Any]
    ) -> None:
        current = lease
        while True:
            await asyncio.sleep(self.refresh_every)
            renewed = self.leases.refresh(current, self.lease_ttl)
            if renewed is None:
                log.warning("lease for %s lost; stopping turn %s", lease.session_id, state.turn_id)
                state.lease_lost = True
                run_task.cancel()
                return
            current = renewed

    # -- event translation ---------------------------------------------------------

    def _translate(self, session_id: str, state: _TurnState, event: Any) -> None:
        turn_id = state.turn_id
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if getattr(data, "type", None) == "response.output_text.delta":
                item_id = data.item_id
                if item_id not in state.open_messages:
                    state.open_messages.add(item_id)
                    self._publish(
                        session_id,
                        "item_started",
                        {"turn_id": turn_id, "item_id": item_id, "kind": "message"},
                    )
                self._publish(
                    session_id,
                    "item_delta",
                    {"turn_id": turn_id, "item_id": item_id, "delta": data.delta},
                )
            return

        if not isinstance(event, RunItemStreamEvent):
            return

        if event.name == "tool_called":
            raw = event.item.raw_item
            call_id = _field(raw, "call_id")
            name = _field(raw, "name") or "tool"
            state.tool_names[call_id] = name
            self._publish(
                session_id,
                "item_started",
                {
                    "turn_id": turn_id,
                    "item_id": call_id,
                    "kind": "tool_call",
                    "name": name,
                    "arguments": _parse(_field(raw, "arguments")),
                },
            )
        elif event.name == "tool_output":
            call_id = _field(event.item.raw_item, "call_id")
            name = state.tool_names.get(call_id, "tool")
            output = _parse(getattr(event.item, "output", None))
            status = output.get("status") if isinstance(output, dict) else None
            if isinstance(output, dict) and output.get("interrupt"):
                state.interrupted = True
            self._publish(
                session_id,
                "item_completed",
                {
                    "turn_id": turn_id,
                    "item_id": call_id,
                    "kind": "tool_call",
                    "name": name,
                    "status": status,
                    "output": output,
                },
            )
            if name.startswith(STATE_TOOL_PREFIXES):
                self._publish_state(session_id, running=True)
        elif event.name == "message_output_created":
            item_id = _field(event.item.raw_item, "id")
            if item_id not in state.open_messages:
                self._publish(
                    session_id,
                    "item_started",
                    {"turn_id": turn_id, "item_id": item_id, "kind": "message"},
                )
            state.open_messages.discard(item_id)
            self._publish(
                session_id,
                "item_completed",
                {
                    "turn_id": turn_id,
                    "item_id": item_id,
                    "kind": "message",
                    "text": ItemHelpers.text_message_output(event.item),
                },
            )

    def _publish(self, session_id: str, type: str, data: dict[str, Any]) -> None:
        self.bus.publish(session_id, type, data)

    def _publish_state(self, session_id: str, *, running: bool) -> None:
        """The full snapshot. A client rebuilds its panel from this, never from prose."""
        plan = self.plans.latest_for_chat(session_id)
        self._publish(
            session_id,
            "state",
            {"running": running, "plan": plan.model_dump(mode="json") if plan else None},
        )


def _duplicate(message: MessageRecord) -> TurnStarted:
    return TurnStarted(message.session_id, message.id, message.turn_id or "", duplicate=True)


def _field(raw: Any, name: str) -> Any:
    return raw.get(name) if isinstance(raw, dict) else getattr(raw, name, None)


def _parse(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
