"""What a tool hands back to the model.

A tool result is a small pydantic model: a status, the ids the model needs to
continue, a one-line summary and, when useful, a hint for the next step. The raw
artefact (the fetched page, the full draft, the rendered file) stays wherever the
tool put it and is referenced by id. That keeps the transcript short and keeps the
model deciding rather than reading.

Errors are results too. A tool never raises at the model boundary: bad arguments
and failed executions both come back as a :class:`ToolError`, which the model can
read and act on (fix the call, try another tool, or tell the user).

Domain code raises :class:`StructuredToolError` when it wants to choose the code
itself — a refused state transition is not the same failure as a crash, and the
model should be able to tell them apart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolErrorCode = Literal[
    # raised at the tool boundary
    "invalid_arguments",
    "tool_failed",
    "invalid_result",
    # raised by the plan domain
    "not_found",
    "role_denied",
    "revision_conflict",
    "illegal_transition",
    "confirmation_required",
    "contract_required",
    "results_required",
]


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


class StructuredToolError(Exception):
    """An exception that carries the :class:`ToolError` it should become.

    Domain services raise these. The registry catches them at the tool boundary
    and turns them into results, so the domain never has to know it is being
    called by a model.
    """

    code: ToolErrorCode = "tool_failed"

    def __init__(
        self,
        message: str,
        *,
        code: ToolErrorCode | None = None,
        issues: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.issues = issues or []
        self.hint = hint

    def as_result(self) -> ToolError:
        return ToolError(code=self.code, message=str(self), issues=self.issues, hint=self.hint)
