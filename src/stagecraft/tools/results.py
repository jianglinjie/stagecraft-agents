"""What a tool hands back to the model.

A tool result is a small pydantic model: a status, the ids the model needs to
continue, a one-line summary and, when useful, a hint for the next step. The raw
artefact (the fetched page, the full draft, the rendered file) stays wherever the
tool put it and is referenced by id. That keeps the transcript short and keeps the
model deciding rather than reading.

Errors are results too. A tool never raises at the model boundary: bad arguments
and failed executions both come back as a :class:`ToolError`, which the model can
read and act on (fix the call, try another tool, or tell the user).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolErrorCode = Literal["invalid_arguments", "tool_failed", "invalid_result"]


class ToolResult(BaseModel):
    """Base class for every tool return value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok", "error"] = "ok"


class ToolError(ToolResult):
    """A structured failure the model can reason about."""

    status: Literal["error"] = "error"
    code: ToolErrorCode
    message: str
    issues: list[str] = Field(default_factory=list)
    hint: str | None = None
