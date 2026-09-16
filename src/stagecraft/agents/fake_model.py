"""A scripted model for tests.

The Agents SDK talks to every model through one interface: ``get_response`` gets
the conversation so far plus the available tools, and answers with output items
(tool calls or an assistant message). :class:`FakeModel` implements that interface
from a script, one *turn* per call, and records what it was shown. Tests can then
run a real ``Runner`` loop with real tool execution and no network, and assert both
what the agent did and what the model saw.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
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


@dataclass(frozen=True)
class WaitFor:
    """Block this model turn until ``event`` is set: lets a test hold a turn mid-flight.

    A ``threading.Event`` rather than an asyncio one, because the turn may be running on
    a server's event loop in another thread.
    """

    event: threading.Event
    timeout: float = 10.0


Step = ToolCall | Reply | WaitFor
Turn = Sequence[Step]


def wait_for(event: threading.Event, timeout: float = 10.0) -> WaitFor:
    return WaitFor(event=event, timeout=timeout)


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


class FakeModel(Model):
    """Replay ``script`` one turn per model call.

    A turn is a list of steps returned together (several ``ToolCall`` steps mean
    parallel tool calls). A bare step is a one-step turn.

    Deliberately not a dataclass: the SDK fingerprints dataclass models with
    ``dataclasses.asdict``, which deep-copies the script, and a ``WaitFor`` step holds
    a lock that cannot be copied.
    """

    def __init__(self, script: Sequence[Step | Turn]) -> None:
        self.script = list(script)
        self.calls: list[RecordedCall] = []
        self._turns: list[list[Step]] = [
            [step] if isinstance(step, ToolCall | Reply | WaitFor) else list(step)
            for step in self.script
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
        steps = self._turns[turn_index]
        for step in steps:
            if isinstance(step, WaitFor):
                released = await asyncio.to_thread(step.event.wait, step.timeout)
                if not released:
                    raise TimeoutError(
                        f"turn {turn_index} waited {step.timeout}s and was not released"
                    )
        output = [
            _output_item(step, turn_index, step_index)
            for step_index, step in enumerate(steps)
            if not isinstance(step, WaitFor)
        ]
        return ModelResponse(output=output, usage=Usage(), response_id=f"fake_resp_{turn_index}")

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
        """The same scripted turn, delivered as text deltas and a completed event."""
        response = await self.get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        sequence = 0
        for output_index, item in enumerate(response.output):
            if not isinstance(item, ResponseOutputMessage):
                continue
            for content_index, part in enumerate(item.content):
                if not isinstance(part, ResponseOutputText):
                    continue
                for chunk in re.findall(r"\S+\s*|\s+", part.text):
                    yield ResponseTextDeltaEvent(
                        type="response.output_text.delta",
                        item_id=item.id,
                        output_index=output_index,
                        content_index=content_index,
                        delta=chunk,
                        logprobs=[],
                        sequence_number=sequence,
                    )
                    sequence += 1
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=sequence,
            response=Response(
                id=response.response_id or "fake_resp",
                created_at=time.time(),
                model="fake",
                object="response",
                output=response.output,
                parallel_tool_calls=True,
                tool_choice="auto",
                tools=[],
            ),
        )


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
