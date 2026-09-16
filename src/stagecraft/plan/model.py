"""Plan and Stage: the shared state the Planner and the Executor write to.

They never message each other. The Planner writes a stage's **contract** (what
must be produced) and the Executor writes its **runtime** (what was produced).
Two halves, two writers, one row. That split is what makes a stage re-executable
without losing the plan, and what lets the Planner resume without having to
invent outputs it never saw.

The Orchestrator owns neither half. It owns the **state**, and moves stages
through the machine in ``state_machine.py``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StageState(StrEnum):
    PENDING = "pending"
    DOING = "doing"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    DONE = "done"
    OMITTED = "omitted"


class ReviewKind(StrEnum):
    """Why a stage is sitting in ``waiting_user``."""

    PLAN_REVIEW = "plan_review"
    RESULT_REVIEW = "result_review"


class WorkItem(BaseModel):
    """One unit of work inside a stage. The Executor reports per item."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    instruction: str


class RuntimeRef(BaseModel):
    """A pointer to something produced. Never the artefact itself."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    kind: str
    summary: str
    work_item_id: str | None = None


class StageContract(BaseModel):
    """Written by the Planner only. The Executor reads it and never edits it."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    inputs: list[str] = Field(
        default_factory=list,
        description="Ids of upstream stages whose runtime refs this stage consumes.",
    )
    work_items: list[WorkItem] = Field(default_factory=list)
    acceptance: str = ""
    sources: list[str] = Field(default_factory=list)


class StageRuntime(BaseModel):
    """Written by the Executor only. Append-only within a stage."""

    model_config = ConfigDict(extra="forbid")

    refs: list[RuntimeRef] = Field(default_factory=list)
    notes: str | None = None
    attempts: int = 0


class Stage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    order: int
    goal: str
    state: StageState = StageState.PENDING
    contract: StageContract | None = None
    runtime: StageRuntime = Field(default_factory=StageRuntime)
    review_kind: ReviewKind | None = None
    blocked_reason: str | None = None
    questions: list[str] = Field(default_factory=list)

    @property
    def pending_items(self) -> list[str]:
        """Work items with no runtime ref yet: what a retry should target."""
        if self.contract is None:
            return []
        produced = {ref.work_item_id for ref in self.runtime.refs}
        return [item.id for item in self.contract.work_items if item.id not in produced]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    chat_id: str
    objective: str
    revision: int = 0
    stages: list[Stage] = Field(default_factory=list)

    def stage(self, stage_id: str) -> Stage | None:
        return next((s for s in self.stages if s.id == stage_id), None)

    def next_pending(self) -> Stage | None:
        return next((s for s in self.ordered() if s.state == StageState.PENDING), None)

    def ordered(self) -> list[Stage]:
        return sorted(self.stages, key=lambda s: s.order)

    def summary(self) -> str:
        """The one-line-per-stage digest that goes into a Turn Context."""
        if not self.stages:
            return f"plan {self.id} (rev {self.revision}): no stages yet"
        rows = [f"  {s.order}. [{s.state}] {s.id} — {s.goal}" for s in self.ordered()]
        return f"plan {self.id} (rev {self.revision}) — {self.objective}\n" + "\n".join(rows)
