"""A scripted model for tests.

The Agents SDK talks to every model through one interface: ``get_response`` gets
the conversation so far plus the available tools, and answers with output items
(tool calls or an assistant message). :class:`FakeModel` implements that interface
from a script, one *turn* per call, and records what it was shown. Tests can then
run a real ``Runner`` loop with real tool execution and no network, and assert both
what the agent did and what the model saw.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolCall:
    """Have the model call ``name`` with ``arguments``."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    """Have the model answer with plain text, ending the run."""

    text: str


Step = ToolCall | Reply
Turn = Sequence[Step]


def tool_call(name: str, /, **arguments: Any) -> ToolCall:
    return ToolCall(name=name, arguments=arguments)


def reply(text: str) -> Reply:
    return Reply(text=text)


def submit(name: str, value: BaseModel) -> ToolCall:
    """Have a sub-agent call its ``submit_*`` tool with ``value``'s fields."""
    return ToolCall(name=name, arguments=value.model_dump(mode="json", exclude={"status"}))


def reply_json(value: BaseModel | dict[str, Any]) -> Reply:
    """A final answer for an agent with a structured ``output_type``."""
    if isinstance(value, BaseModel):
        return Reply(text=value.model_dump_json())
    return Reply(text=json.dumps(value, ensure_ascii=False))


@dataclass
class RecordedCall:
    """One ``get_response`` call as the model saw it."""

    system_instructions: str | None
    input: str | list[TResponseInputItem]
    tool_names: list[str]

    def tool_outputs(self) -> list[dict[str, Any]]:
        """The ``function_call_output`` items in this call's input, parsed as JSON."""
        if isinstance(self.input, str):
            return []
        outputs: list[dict[str, Any]] = []
        for item in self.input:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                raw = item.get("output")
                outputs.append(json.loads(raw) if isinstance(raw, str) else dict(raw))  # type: ignore[arg-type]
        return outputs


class ScriptExhaustedError(RuntimeError):
    """The runner asked for another model turn but the script has none left."""


@dataclass
class FakeModel(Model):
    """Replay ``script`` one turn per model call.

    A turn is a list of steps returned together (several ``ToolCall`` steps mean
    parallel tool calls). A bare step is a one-step turn.
    """

    script: Sequence[Step | Turn]
    calls: list[RecordedCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._turns: list[list[Step]] = [
            [step] if isinstance(step, ToolCall | Reply) else list(step) for step in self.script
        ]
        self._cursor = 0

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self._turns)

    @property
    def remaining_turns(self) -> int:
        return len(self._turns) - self._cursor

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        self.calls.append(
            RecordedCall(
                system_instructions=system_instructions,
                input=input,
                tool_names=[getattr(t, "name", type(t).__name__) for t in tools],
            )
        )
        if self.exhausted:
            raise ScriptExhaustedError(
                f"model called {len(self.calls)} times but the script has {len(self._turns)} turns"
            )
        turn_index = self._cursor
        self._cursor += 1
        output = [
            _output_item(step, turn_index, step_index)
            for step_index, step in enumerate(self._turns[turn_index])
        ]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("FakeModel supports Runner.run, not streaming")
        yield  # pragma: no cover - makes this an async generator


def _output_item(step: Step, turn_index: int, step_index: int) -> Any:
    item_id = f"fake_{turn_index}_{step_index}"
    if isinstance(step, ToolCall):
        return ResponseFunctionToolCall(
            id=item_id,
            call_id=item_id,
            type="function_call",
            name=step.name,
            arguments=json.dumps(step.arguments, ensure_ascii=False),
        )
    return ResponseOutputMessage(
        id=item_id,
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=step.text, annotations=[])],
    )
