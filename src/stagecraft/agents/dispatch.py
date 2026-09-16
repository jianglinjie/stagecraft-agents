"""The three dispatch tools: agent-as-tool, not handoff.

A handoff passes the conversation to another agent, which then owns it. A dispatch
starts a separate run with an explicit payload, waits for it, and returns a small
result to the Orchestrator, which stays in charge of the user.

Two rules make the pattern safe:

* **payload only** — the sub-agent's whole input is the payload model below. It
  never sees the parent conversation, so it cannot be steered by it and does not
  pay for it in tokens.
* **trust the store, not the claim** — after a sub-run, the dispatch tool re-reads
  the Plan Store and reports what is actually there. A Planner that says it wrote
  three stages but wrote two is reported as two.

Only the Orchestrator may dispatch. Sub-agents hold no dispatch tools, and the
role check below refuses anyone else who somehow calls one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from stagecraft.agents import executor, planner, router
from stagecraft.agents.executor import ExecutorOutput, ExecutorPayload, build_executor
from stagecraft.agents.planner import PlannerOutput, PlannerPayload, build_planner
from stagecraft.agents.router import RouterPayload, RoutingCapsule, build_router
from stagecraft.agents.submit import build_submit_tool, read_submission
from stagecraft.plan import IllegalTransition, RoleDenied, RuntimeRef, StageState
from stagecraft.tools.context import RunContext
from stagecraft.tools.registry import ToolSpec, tool
from stagecraft.tools.results import ToolResult

if TYPE_CHECKING:
    from stagecraft.agents.runtime import AgentRuntime


class RouteDecision(ToolResult):
    route: Literal["direct", "workflow"]
    reason: str
    next_action: str


class PlannerDispatched(ToolResult):
    plan_id: str
    task_id: str
    revision: int
    authored_stage_ids: list[str]
    questions: list[str]
    interrupt: bool
    summary: str
    plan_summary: str
    next_action: str


class ExecutorDispatched(ToolResult):
    plan_id: str
    stage_id: str
    state: StageState
    refs: list[RuntimeRef]
    pending_items: list[str]
    failed_items: list[str]
    summary: str
    next_action: str


def build_submit_tools() -> list[ToolSpec]:
    """How each sub-agent hands its result back. See ``submit.py`` for why a tool."""
    return [
        build_submit_tool(
            RoutingCapsule,
            name=router.SUBMIT_TOOL,
            description="Submit the routing decision. This ends your run.",
        ),
        build_submit_tool(
            PlannerOutput,
            name=planner.SUBMIT_TOOL,
            description="Submit the authoring result. This ends your run.",
        ),
        build_submit_tool(
            ExecutorOutput,
            name=executor.SUBMIT_TOOL,
            description="Submit the execution result. This ends your run.",
        ),
    ]


def build_dispatch_tools(runtime: AgentRuntime) -> list[ToolSpec]:
    store = runtime.store

    @tool
    async def dispatch_router(
        ctx: RunContext,
        request: Annotated[str, "The user's request, verbatim or lightly condensed."],
    ) -> RouteDecision:
        """Ask the Router whether a request is direct work or needs a staged plan."""
        _require_orchestrator(ctx, "dispatch_router")
        result = await runtime.run_sub_agent(
            build_router(runtime), RouterPayload(request=request), ctx.as_role("router")
        )
        capsule = read_submission(result, RoutingCapsule, role="router")
        return RouteDecision(
            route=capsule.route,
            reason=capsule.reason,
            next_action=(
                "do the work with the content tools, then reply"
                if capsule.route == "direct"
                else "plan_create, then dispatch_planner"
            ),
        )

    @tool
    async def dispatch_planner(
        ctx: RunContext,
        plan_id: Annotated[str, "Plan to author into."],
        goal: Annotated[str, "What the plan must achieve."],
        stage_id: Annotated[str | None, "Rewrite only this stage."] = None,
        answers: Annotated[list[str], "The user's answers to the Planner's questions."] = [],  # noqa: B006
        task_id: Annotated[
            str | None, "Resume this Planner task. Omit to use the plan's own planner task."
        ] = None,
    ) -> PlannerDispatched:
        """Ask the Planner to author stages into the Plan Store. It does not execute them.

        The Planner keeps its own conversation per task, so a second dispatch with the same
        task continues where the last one stopped, including across restarts.
        """
        _require_orchestrator(ctx, "dispatch_planner")
        before = {stage.id for stage in store.get_plan(plan_id).stages}
        if answers:
            store.clear_questions(plan_id=plan_id, role=ctx.role)
        task = task_id or f"{plan_id}:planner"
        payload = PlannerPayload(plan_id=plan_id, goal=goal, stage_id=stage_id, answers=answers)
        memory = runtime.memory(f"task:{task}", "planner")
        await memory.maybe_compact(runtime.compactor)
        result = await runtime.run_sub_agent(
            build_planner(runtime), payload, ctx.as_role("planner"), session=memory
        )
        output = read_submission(result, PlannerOutput, role="planner")

        if output.questions:
            store.ask_user(plan_id=plan_id, role=ctx.role, questions=output.questions)
        plan = store.get_plan(plan_id)
        authored = [stage.id for stage in plan.ordered() if stage.id not in before]
        if output.questions:
            action = "the turn ends here: the user must answer these questions first"
        elif plan.next_pending() is not None:
            action = "request waiting_user with review_kind=plan_review and present the stage"
        else:
            action = "no pending stage; tell the user what the plan contains"
        return PlannerDispatched(
            plan_id=plan.id,
            task_id=task,
            revision=plan.revision,
            authored_stage_ids=authored,
            questions=output.questions,
            interrupt=bool(output.questions),
            summary=output.summary,
            plan_summary=plan.summary(),
            next_action=action,
        )

    @tool
    async def dispatch_executor(
        ctx: RunContext,
        plan_id: Annotated[str, "Plan id."],
        stage_id: Annotated[str, "Stage to execute. It must already be in doing."],
        order: Annotated[int, "The stage's order, for the Executor's orientation."],
        goal: Annotated[str, "The stage goal, copied from the plan."],
        retry_ids: Annotated[list[str], "Only re-run these work items."] = [],  # noqa: B006
    ) -> ExecutorDispatched:
        """Run the Executor on one stage. Pass the pointer only, never contracts or content."""
        _require_orchestrator(ctx, "dispatch_executor")
        _, stage = store.get_stage(plan_id, stage_id)
        if stage.state != StageState.DOING:
            raise IllegalTransition(
                f"stage {stage_id} is {stage.state}; only a stage in doing can be executed",
                hint="Get the user's approval and request doing with user_confirmed=true first.",
            )
        payload = ExecutorPayload(
            plan_id=plan_id, stage_id=stage_id, order=order, goal=goal, retry_ids=retry_ids
        )
        result = await runtime.run_sub_agent(
            build_executor(runtime), payload, ctx.as_role("executor")
        )
        output = read_submission(result, ExecutorOutput, role="executor")

        _, stage = store.get_stage(plan_id, stage_id)
        if stage.pending_items:
            action = (
                f"retry once with retry_ids={stage.pending_items}; if still pending, request "
                "blocked with a reason"
            )
        else:
            action = "request done, or waiting_user with review_kind=result_review"
        return ExecutorDispatched(
            plan_id=plan_id,
            stage_id=stage_id,
            state=stage.state,
            refs=list(stage.runtime.refs),
            pending_items=stage.pending_items,
            failed_items=output.failed_item_ids,
            summary=output.summary,
            next_action=action,
        )

    return [dispatch_router, dispatch_planner, dispatch_executor]


def _require_orchestrator(ctx: RunContext, tool_name: str) -> None:
    if ctx.role != "orchestrator":
        raise RoleDenied(
            f"role {ctx.role!r} may not call {tool_name}",
            hint="Sub-agents return to the orchestrator; they never dispatch.",
        )
