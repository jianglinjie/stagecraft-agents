"""Plan tools: the only way an agent touches the Plan Store.

Five tools, and which role may use each write is decided by the store, not by
which agent happens to hold the tool. The role comes from the run context, so a
model cannot claim to be someone else.

* ``plan_create``               orchestrator
* ``plan_get_stage_detail``     everyone (read)
* ``plan_write_stage_contract`` planner
* ``plan_attach_runtime``       executor
* ``plan_update_stage_state``   orchestrator

Every write returns the new revision, the stage's state, what is still pending,
and ``next_action`` — the model's next step comes from the store, not from its
own reading of the transcript.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from stagecraft.plan import (
    Plan,
    PlanStore,
    ReviewKind,
    RuntimeRef,
    Stage,
    StageContract,
    StageState,
    WorkItem,
    next_action,
)
from stagecraft.tools.context import RunContext
from stagecraft.tools.registry import ToolSpec, tool
from stagecraft.tools.results import ToolResult

ExpectedRevision = Annotated[
    int | None,
    Field(
        default=None,
        description="Revision you last read. If the plan moved since, the write is refused.",
    ),
]


class PlanCreated(ToolResult):
    plan_id: str
    revision: int
    next_action: str


class StageWritten(ToolResult):
    plan_id: str
    revision: int
    stage_id: str
    order: int
    state: StageState
    review_kind: ReviewKind | None
    pending_items: list[str]
    next_action: str
    plan_summary: str


class StageDetail(ToolResult):
    plan_id: str
    revision: int
    stage_id: str
    order: int
    goal: str
    state: StageState
    review_kind: ReviewKind | None
    contract: StageContract | None
    upstream_refs: list[RuntimeRef]
    unresolved_inputs: list[str]
    runtime_refs: list[RuntimeRef]
    pending_items: list[str]
    questions: list[str]
    next_action: str


def stage_written(plan: Plan, stage: Stage) -> StageWritten:
    return StageWritten(
        plan_id=plan.id,
        revision=plan.revision,
        stage_id=stage.id,
        order=stage.order,
        state=stage.state,
        review_kind=stage.review_kind,
        pending_items=stage.pending_items,
        next_action=next_action(stage),
        plan_summary=plan.summary(),
    )


def stage_detail(plan: Plan, stage: Stage) -> StageDetail:
    """Contract plus resolved pointers: everything an Executor needs, nothing it doesn't."""
    upstream: list[RuntimeRef] = []
    unresolved: list[str] = []
    for input_id in stage.contract.inputs if stage.contract else []:
        source = plan.stage(input_id)
        if source is None or not source.runtime.refs:
            unresolved.append(input_id)
        else:
            upstream.extend(source.runtime.refs)
    return StageDetail(
        plan_id=plan.id,
        revision=plan.revision,
        stage_id=stage.id,
        order=stage.order,
        goal=stage.goal,
        state=stage.state,
        review_kind=stage.review_kind,
        contract=stage.contract,
        upstream_refs=upstream,
        unresolved_inputs=unresolved,
        runtime_refs=list(stage.runtime.refs),
        pending_items=stage.pending_items,
        questions=list(stage.questions),
        next_action=next_action(stage),
    )


def build_plan_tools(store: PlanStore) -> list[ToolSpec]:
    @tool
    def plan_create(
        ctx: RunContext,
        objective: Annotated[str, "What the whole plan must achieve, in one sentence."],
    ) -> PlanCreated:
        """Create an empty plan for this chat. Stages are authored by the Planner."""
        plan = store.create_plan(chat_id=ctx.chat_id, objective=objective, role=ctx.role)
        return PlanCreated(
            plan_id=plan.id,
            revision=plan.revision,
            next_action="dispatch_planner with this plan_id to author the stages",
        )

    @tool
    def plan_get_stage_detail(
        plan_id: Annotated[str, "Plan id."],
        stage_id: Annotated[str, "Stage id."],
    ) -> StageDetail:
        """Read one stage: its contract, resolved upstream refs, produced refs and next action."""
        plan, stage = store.get_stage(plan_id, stage_id)
        return stage_detail(plan, stage)

    @tool
    def plan_write_stage_contract(
        ctx: RunContext,
        plan_id: Annotated[str, "Plan id."],
        goal: Annotated[str, "What this stage must produce."],
        work_items: Annotated[
            list[WorkItem], "Concrete units of work. Ids must be unique within the stage."
        ],
        inputs: Annotated[list[str], "Ids of upstream stages whose outputs this consumes."] = [],  # noqa: B006
        acceptance: Annotated[str, "How to tell the stage is done."] = "",
        stage_id: Annotated[
            str | None, "Omit to author a new stage; pass an id to rewrite a pending one."
        ] = None,
        order: Annotated[int | None, "Position in the plan. Omit to append."] = None,
        expected_revision: ExpectedRevision = None,
    ) -> StageWritten:
        """Author one stage's contract. Write exactly one stage per call."""
        contract = StageContract(
            goal=goal, inputs=inputs, work_items=work_items, acceptance=acceptance
        )
        plan, stage = store.write_stage_contract(
            plan_id=plan_id,
            role=ctx.role,
            contract=contract,
            stage_id=stage_id,
            order=order,
            expected_revision=expected_revision,
        )
        return stage_written(plan, stage)

    @tool
    def plan_attach_runtime(
        ctx: RunContext,
        plan_id: Annotated[str, "Plan id."],
        stage_id: Annotated[str, "Stage id, which must be in doing."],
        refs: Annotated[list[RuntimeRef], "Ids returned by content tools, one per work item."],
        notes: Annotated[str | None, "Anything the orchestrator should know."] = None,
        expected_revision: ExpectedRevision = None,
    ) -> StageWritten:
        """Record what this stage produced. Pointers only, never content."""
        plan, stage = store.attach_runtime(
            plan_id=plan_id,
            stage_id=stage_id,
            role=ctx.role,
            refs=refs,
            notes=notes,
            expected_revision=expected_revision,
        )
        return stage_written(plan, stage)

    @tool
    def plan_update_stage_state(
        ctx: RunContext,
        plan_id: Annotated[str, "Plan id."],
        stage_id: Annotated[str, "Stage id."],
        target: Annotated[StageState, "The state to move to."],
        user_confirmed: Annotated[
            bool, "True only if the user explicitly approved in their latest message."
        ] = False,
        review_kind: Annotated[ReviewKind | None, "Required when target is waiting_user."] = None,
        blocked_reason: Annotated[str | None, "Required when target is blocked."] = None,
        questions: Annotated[list[str], "Questions for the user, when waiting on them."] = [],  # noqa: B006
        expected_revision: ExpectedRevision = None,
    ) -> StageWritten:
        """Move a stage through the state machine. Illegal moves are refused with a hint."""
        plan, stage = store.update_stage_state(
            plan_id=plan_id,
            stage_id=stage_id,
            role=ctx.role,
            target=target,
            user_confirmed=user_confirmed,
            review_kind=review_kind,
            blocked_reason=blocked_reason,
            questions=questions,
            expected_revision=expected_revision,
        )
        return stage_written(plan, stage)

    return [
        plan_create,
        plan_get_stage_detail,
        plan_write_stage_contract,
        plan_attach_runtime,
        plan_update_stage_state,
    ]
