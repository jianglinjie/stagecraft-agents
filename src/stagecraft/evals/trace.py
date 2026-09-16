"""What a case run observed.

Three recorders:

* :class:`MeteredModel` wraps every role's model, counts requests and tokens, and notices
  infrastructure failures (timeouts, rate limits, 5xx) even when a dispatch tool turns one
  into a ``tool_failed`` result the orchestrator can see. A case that met one is reported
  as an error and retried, never as a model failure. A refused account (401, 402, 403) is
  worse: every later request fails the same way, so it aborts the whole run.
* :class:`CaseSession` sends user turns through the real orchestrator and records every
  tool call of every role, with the arguments as the tool saw them (defaults applied).
* each :class:`TurnRecord` keeps the plan and workspace as they were when that turn
  ended, so a turn's checks see that turn's state, not the case's final state.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import openai
from agents import Model, RunResult
from agents.exceptions import AgentsException
from agents.items import ModelResponse, RunItem
from pydantic import BaseModel, ValidationError

from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.runtime import AgentRuntime
from stagecraft.plan import Plan, PlanNotFound
from stagecraft.tools.context import Role

INFRA_ERRORS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,  # includes APITimeoutError
    openai.RateLimitError,
    openai.InternalServerError,
)
#: Bad key, no balance, no access: retrying cannot help and nothing after it measures anything.
FATAL_STATUS = frozenset({401, 402, 403})


class EndpointRefused(RuntimeError):
    """The endpoint refused the account. The eval run must stop, not score the refusals."""


def is_fatal(err: BaseException) -> bool:
    return isinstance(err, openai.APIStatusError) and err.status_code in FATAL_STATUS


@dataclass
class RoleUsage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Meter:
    usage: dict[str, RoleUsage] = field(default_factory=dict)
    infra_errors: list[str] = field(default_factory=list)
    refused: str | None = None

    def add(self, role: str, response: ModelResponse) -> None:
        entry = self.usage.setdefault(role, RoleUsage())
        entry.requests += 1
        entry.input_tokens += response.usage.input_tokens
        entry.output_tokens += response.usage.output_tokens

    def total(self) -> RoleUsage:
        total = RoleUsage()
        for entry in self.usage.values():
            total.requests += entry.requests
            total.input_tokens += entry.input_tokens
            total.output_tokens += entry.output_tokens
        return total


class MeteredModel(Model):
    """Delegates to ``inner``; counts what it costs and what failed underneath it."""

    def __init__(self, inner: Model, role: str, meter: Meter) -> None:
        self.inner = inner
        self.role = role
        self.meter = meter

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        try:
            response = await self.inner.get_response(*args, **kwargs)
        except INFRA_ERRORS as err:
            self.meter.infra_errors.append(f"{self.role}: {type(err).__name__}: {err}")
            raise
        except openai.APIStatusError as err:
            if is_fatal(err):
                self.meter.refused = f"{self.role}: HTTP {err.status_code}: {err.message}"
            raise
        self.meter.add(self.role, response)
        return response

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner.stream_response(*args, **kwargs)

    def get_retry_advice(self, request: Any) -> Any:
        return self.inner.get_retry_advice(request)

    async def close(self) -> None:
        await self.inner.close()


@dataclass
class ToolCallRecord:
    role: str
    name: str
    arguments: dict[str, Any]
    output: dict[str, Any] | None = None

    @property
    def error_code(self) -> str | None:
        if self.output and self.output.get("status") == "error":
            return str(self.output.get("code"))
        return None

    def payload(self) -> str:
        """What a dispatch told its sub-agent: the part of the payload worth reading in a report."""
        keys = ("request", "goal", "stage_id", "answers", "retry_ids")
        shown = {key: self.arguments[key] for key in keys if self.arguments.get(key)}
        return f"{self.name} {json.dumps(shown, ensure_ascii=False)}"

    def label(self) -> str:
        """``name``, a telling argument for a few tools, and ``!code`` when it failed."""
        hint_key = {
            "plan_update_stage_state": "target",
            "render_output": "format",
            "write_draft": "tone",
            "write_outline": "sections",
        }.get(self.name)
        text = self.name
        if hint_key and hint_key in self.arguments:
            text += f"({self.arguments[hint_key]})"
        if self.error_code:
            text += f"!{self.error_code}"
        return text


@dataclass
class TurnRecord:
    index: int
    user: str
    auto: bool
    output: str | None = None
    error: str | None = None
    calls: list[ToolCallRecord] = field(default_factory=list)
    plan: Plan | None = None
    workspace: dict[str, dict[str, Any]] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def interrupted(self) -> bool:
        return any(
            call.role == "orchestrator" and call.output and call.output.get("interrupt") is True
            for call in self.calls
        )

    def calls_by_role(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for call in self.calls:
            grouped.setdefault(call.role, []).append(call.label())
        return grouped


class CaseSession:
    """One eval chat: sends turns through the orchestrator with session memory on."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self.turns: list[TurnRecord] = []
        self.plan_ids: set[str] = set()
        self._pending: list[ToolCallRecord] = []
        runtime.on_sub_run = self._on_sub_run

    async def send(self, text: str, *, auto: bool = False) -> TurnRecord:
        turn = TurnRecord(index=len(self.turns) + 1, user=text, auto=auto)
        self.turns.append(turn)
        self._pending = []
        started = time.monotonic()
        items: Sequence[RunItem] = ()
        try:
            result = await run_orchestrator_turn(self.runtime, text, remember=True)
            items = result.new_items
            turn.output = _text(result.final_output)
        except INFRA_ERRORS:
            raise
        except openai.APIStatusError as err:
            if is_fatal(err):
                raise
            turn.error = f"{type(err).__name__}: {err}"
        except AgentsException as err:
            # The model misbehaved (too many turns, a tool it does not hold, bad JSON):
            # that is the agent failing the case, so keep what it did before failing.
            turn.error = f"{type(err).__name__}: {err}"
            if err.run_data is not None:
                items = err.run_data.new_items
        except Exception as err:  # noqa: BLE001 - one broken case must not end the suite
            turn.error = f"{type(err).__name__}: {err}"
        turn.calls = [*self._pending, *self._calls("orchestrator", items)]
        turn.seconds = time.monotonic() - started
        for call in turn.calls:
            if call.name == "plan_create" and call.output and call.output.get("plan_id"):
                self.plan_ids.add(str(call.output["plan_id"]))
        turn.plan = self.plan()
        turn.workspace = self.runtime.workspace.snapshot()
        return turn

    def plan(self) -> Plan | None:
        return self.runtime.store.latest_for_chat(self.runtime.chat_id)

    def plans(self) -> list[Plan]:
        found: list[Plan] = []
        for plan_id in sorted(self.plan_ids):
            try:
                found.append(self.runtime.store.get_plan(plan_id))
            except PlanNotFound:
                continue
        return found

    def _on_sub_run(self, role: Role, _payload: BaseModel, result: RunResult) -> None:
        self._pending.extend(self._calls(role, result.new_items))

    def _calls(self, role: str, items: Sequence[RunItem]) -> list[ToolCallRecord]:
        records: list[ToolCallRecord] = []
        by_call_id: dict[str, ToolCallRecord] = {}
        for item in items:
            if item.type == "tool_call_item":
                raw = item.raw_item
                name = str(getattr(raw, "name", "") or getattr(raw, "type", "tool"))
                record = ToolCallRecord(
                    role=role,
                    name=name,
                    arguments=self._effective_arguments(name, getattr(raw, "arguments", "")),
                )
                records.append(record)
                call_id = getattr(raw, "call_id", None)
                if call_id:
                    by_call_id[call_id] = record
            elif item.type == "tool_call_output_item":
                raw_output = item.raw_item
                call_id = (
                    raw_output.get("call_id")
                    if isinstance(raw_output, Mapping)
                    else getattr(raw_output, "call_id", None)
                )
                record = by_call_id.get(str(call_id))
                if record is not None:
                    record.output = _json_object(item.output)
        return records

    def _effective_arguments(self, name: str, raw: str) -> dict[str, Any]:
        arguments = _json_object(raw) or {}
        if name not in self.runtime.registry:
            return arguments
        spec = self.runtime.registry.get(name)
        try:
            params = spec.params_model.model_validate(arguments)
        except ValidationError:
            return arguments
        return {**params.model_dump(mode="json"), **(params.model_extra or {})}


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(_json_object(value) or str(value))
