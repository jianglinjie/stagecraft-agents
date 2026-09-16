"""Sub-agents return structured results by calling a ``submit_*`` tool.

The SDK's ``output_type`` asks Chat Completions for ``response_format: json_schema``,
which many OpenAI-compatible endpoints refuse (DeepSeek answers HTTP 400). Function
calling works everywhere this project targets, so a sub-agent ends its run by
calling its submit tool instead:

* the arguments are validated against the output model like any tool call;
* an invalid submission comes back as a ``ToolError`` and the run continues, so
  the model can fix it;
* a valid submission ends the run, and its JSON is the run's final output.

A sub-agent that ends with plain text instead of submitting is reported to the
Orchestrator as a failed dispatch, never silently accepted.
"""

from __future__ import annotations

import json
from typing import Any

from agents import FunctionToolResult, RunContextWrapper, RunResult, ToolsToFinalOutputResult
from pydantic import ConfigDict, ValidationError, create_model

from stagecraft.tools.registry import ToolSpec
from stagecraft.tools.results import StructuredToolError, ToolResult


def build_submit_tool(output_model: type[ToolResult], *, name: str, description: str) -> ToolSpec:
    """A tool whose parameters are ``output_model``'s fields and whose result is the model."""
    fields: dict[str, Any] = {
        field_name: (info.annotation, info)
        for field_name, info in output_model.model_fields.items()
        if field_name != "status"
    }
    params_model = create_model(
        f"{output_model.__name__}Submission",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )

    def submit(**kwargs: Any) -> ToolResult:
        return output_model(**kwargs)

    return ToolSpec(
        name=name,
        description=description,
        fn=submit,
        params_model=params_model,
        result_model=output_model,
        is_async=False,
    )


def stop_on_submit(tool_name: str):  # type: ignore[no-untyped-def]
    """``tool_use_behavior`` that ends the run on the first *valid* submission only."""

    def behavior(
        _ctx: RunContextWrapper[Any], results: list[FunctionToolResult]
    ) -> ToolsToFinalOutputResult:
        for result in results:
            if result.tool.name != tool_name:
                continue
            if _status(result.output) == "ok":
                return ToolsToFinalOutputResult(is_final_output=True, final_output=result.output)
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

    return behavior


def read_submission[T: ToolResult](result: RunResult, output_model: type[T], *, role: str) -> T:
    raw = result.final_output
    if isinstance(raw, output_model):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("status") == "ok":
            try:
                return output_model.model_validate(data)
            except ValidationError:
                pass
    said = str(raw)[:160]
    raise StructuredToolError(
        f"{role} ended without a valid submission (it said: {said!r})",
        code="tool_failed",
        hint=f"Dispatch the {role} once more; if it fails again, tell the user.",
    )


def _status(output: Any) -> str | None:
    if isinstance(output, ToolResult):
        return output.status
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return None
        return data.get("status") if isinstance(data, dict) else None
    return None
