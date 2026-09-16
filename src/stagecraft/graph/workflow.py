"""The same four-role flow as a LangGraph ``StateGraph``.

What moved, compared with the Agents SDK version:

* **control flow** is the graph's edges, not an orchestrator model reading
  ``next_action``. No model decides whether to execute; an edge does.
* **state** is the graph state, checkpointed per ``thread_id``. The plan lives there,
  not in a separate store the agents query through tools.
* **waiting on the user** is ``interrupt()``: the graph stops, the checkpoint records
  where, and ``Command(resume=...)`` continues from that node, in this process or
  after a restart.

What did not move: the stage state machine. Every state change still goes through
``check_transition``, so the gate (no edge from pending to doing, confirmation to leave
review) holds here too::

    START -> route -> direct -> END
                   -> plan -> ask (interrupt) -> plan
                           -> present_review -> await_review (interrupt) -> plan | execute
                                                execute -> execute (retry) | present_review | finish
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from stagecraft.agents.router import RoutingCapsule
from stagecraft.graph.agents import GraphDeps, GraphExecutorOutput, GraphPlannerOutput
from stagecraft.plan import (
    Plan,
    ReviewKind,
    Stage,
    StageContract,
    StageState,
    check_transition,
)

S = StageState


class GraphState(TypedDict, total=False):
    request: str
    route: Literal["direct", "workflow"]
    route_reason: str
    plan: dict[str, Any]
    questions: list[str]
    answers: list[str]
    review_stage_id: str | None
    feedback: str | None
    output: str
    log: Annotated[list[str], operator.add]


def build_graph(deps: GraphDeps, checkpointer: BaseCheckpointSaver | None = None) -> Any:
    async def route(state: GraphState) -> GraphState:
        capsule = await deps.submit("router", {"request": state["request"]}, RoutingCapsule)
        return {
            "route": capsule.route,
            "route_reason": capsule.reason,
            "log": [f"route: {capsule.route}"],
        }

    async def direct(state: GraphState) -> GraphState:
        result = await deps.run("direct", state["request"])
        return {"output": str(result.final_output), "log": ["direct: done"]}

    async def plan(state: GraphState) -> GraphState:
        current = _plan(state)
        revising = current.stage(state["review_stage_id"]) if state.get("review_stage_id") else None
        payload: dict[str, Any] = {"goal": state["request"], "answers": state.get("answers", [])}
        if revising is not None and state.get("feedback"):
            payload["revise"] = revising.model_dump(mode="json", include={"id", "goal", "contract"})
            payload["feedback"] = state["feedback"]

        output = await deps.submit("planner", payload, GraphPlannerOutput)
        if output.questions:
            return {"questions": output.questions, "log": ["planner asked questions"]}

        if revising is not None and state.get("feedback"):
            (draft,) = output.stages[:1]
            revising.contract = _contract(draft, current)
            revising.goal = draft.goal
            revising.questions = []
            note = f"revised {revising.id}"
        else:
            for draft in output.stages:
                order = len(current.stages) + 1
                current.stages.append(
                    Stage(
                        id=f"stage_{order:02d}",
                        order=order,
                        goal=draft.goal,
                        contract=_contract(draft, current),
                    )
                )
            note = f"authored {len(output.stages)} stages"
        current.revision += 1
        return {
            "plan": _dump(current),
            "questions": [],
            "answers": [],
            "feedback": None,
            "log": [note],
        }

    async def ask(state: GraphState) -> GraphState:
        answers = interrupt({"type": "questions", "questions": state["questions"]})
        return {"answers": list(answers), "questions": [], "log": ["answers received"]}

    async def present_review(state: GraphState) -> GraphState:
        current = _plan(state)
        stage = next(
            (
                s
                for s in current.ordered()
                if s.state == S.PENDING
                or (s.state == S.WAITING_USER and s.review_kind == ReviewKind.PLAN_REVIEW)
            ),
            None,
        )
        if stage is None:
            return {"review_stage_id": None, "log": ["nothing left to review"]}
        if stage.state == S.PENDING:
            check_transition(stage, S.WAITING_USER, review_kind=ReviewKind.PLAN_REVIEW)
            stage.state, stage.review_kind = S.WAITING_USER, ReviewKind.PLAN_REVIEW
            current.revision += 1
        return {"plan": _dump(current), "review_stage_id": stage.id, "log": [f"review {stage.id}"]}

    async def await_review(state: GraphState) -> GraphState:
        current = _plan(state)
        stage = _stage(current, state["review_stage_id"])
        decision = interrupt(
            {"type": "plan_review", "stage": stage.model_dump(mode="json", exclude={"runtime"})}
        )
        if not decision.get("approved"):
            return {"feedback": decision.get("feedback") or "revise", "log": [f"revise {stage.id}"]}
        check_transition(stage, S.DOING, user_confirmed=True)
        stage.state, stage.review_kind = S.DOING, None
        current.revision += 1
        return {"plan": _dump(current), "feedback": None, "log": [f"approved {stage.id}"]}

    async def execute(state: GraphState) -> GraphState:
        current = _plan(state)
        stage = _stage(current, state["review_stage_id"])
        assert stage.contract is not None
        upstream = [
            ref for sid in stage.contract.inputs for ref in _stage(current, sid).runtime.refs
        ]
        output = await deps.submit(
            "executor",
            {
                "goal": stage.goal,
                "work_items": [w.model_dump() for w in stage.contract.work_items],
                "acceptance": stage.contract.acceptance,
                "upstream_refs": [r.model_dump() for r in upstream],
                "retry_ids": stage.pending_items if stage.runtime.attempts else [],
            },
            GraphExecutorOutput,
        )
        known = {r.ref_id for r in stage.runtime.refs}
        stage.runtime.refs.extend(r for r in output.refs if r.ref_id not in known)
        stage.runtime.attempts += 1

        if not stage.pending_items:
            check_transition(stage, S.DONE)
            stage.state, note = S.DONE, f"done {stage.id}"
        elif stage.runtime.attempts >= deps.max_attempts:
            check_transition(stage, S.BLOCKED)
            stage.state = S.BLOCKED
            stage.blocked_reason = f"unfinished after {stage.runtime.attempts} attempts"
            note = f"blocked {stage.id}"
        else:
            note = f"retry {stage.id}: {stage.pending_items}"
        current.revision += 1
        return {
            "plan": _dump(current),
            "review_stage_id": stage.id if stage.state == S.DOING else None,
            "log": [note],
        }

    async def finish(state: GraphState) -> GraphState:
        current = _plan(state)
        return {"output": current.summary(), "log": ["finish"]}

    builder = StateGraph(GraphState)
    for name, node in (
        ("route", route),
        ("direct", direct),
        ("plan", plan),
        ("ask", ask),
        ("present_review", present_review),
        ("await_review", await_review),
        ("execute", execute),
        ("finish", finish),
    ):
        builder.add_node(name, node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route", lambda s: "direct" if s["route"] == "direct" else "plan", ["direct", "plan"]
    )
    builder.add_edge("direct", END)
    builder.add_conditional_edges(
        "plan",
        lambda s: "ask" if s.get("questions") else "present_review",
        ["ask", "present_review"],
    )
    builder.add_edge("ask", "plan")
    builder.add_conditional_edges(
        "present_review",
        lambda s: "await_review" if s.get("review_stage_id") else "finish",
        ["await_review", "finish"],
    )
    builder.add_conditional_edges(
        "await_review", lambda s: "plan" if s.get("feedback") else "execute", ["plan", "execute"]
    )
    builder.add_conditional_edges(
        "execute", _after_execute, ["execute", "present_review", "finish"]
    )
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)


def _after_execute(state: GraphState) -> str:
    if state.get("review_stage_id"):
        return "execute"
    current = _plan(state)
    if any(s.state == S.PENDING for s in current.stages):
        return "present_review"
    return "finish"


@dataclass(frozen=True)
class GraphTurn:
    """What a caller gets back from starting or resuming a thread."""

    interrupt: dict[str, Any] | None
    output: str | None
    plan: Plan | None
    log: list[str]


async def start_thread(graph: Any, thread_id: str, request: str) -> GraphTurn:
    config = {"configurable": {"thread_id": thread_id}}
    return _turn(await graph.ainvoke({"request": request, "log": []}, config))


async def resume_thread(graph: Any, thread_id: str, value: Any) -> GraphTurn:
    config = {"configurable": {"thread_id": thread_id}}
    return _turn(await graph.ainvoke(Command(resume=value), config))


def _turn(values: dict[str, Any]) -> GraphTurn:
    interrupts = values.get("__interrupt__") or []
    plan = Plan.model_validate(values["plan"]) if values.get("plan") else None
    return GraphTurn(
        interrupt=interrupts[0].value if interrupts else None,
        output=None if interrupts else values.get("output"),
        plan=plan,
        log=list(values.get("log", [])),
    )


def _plan(state: GraphState) -> Plan:
    if state.get("plan"):
        return Plan.model_validate(state["plan"])
    return Plan(id="plan_graph", chat_id="graph", objective=state.get("request", ""))


def _dump(plan: Plan) -> dict[str, Any]:
    return plan.model_dump(mode="json")


def _stage(plan: Plan, stage_id: str | None) -> Stage:
    stage = plan.stage(stage_id or "")
    if stage is None:
        raise KeyError(f"no stage {stage_id!r} in the graph state")
    return stage


def _contract(draft: Any, plan: Plan) -> StageContract:
    ordered = plan.ordered()
    inputs = [ordered[i - 1].id for i in draft.inputs if 0 < i <= len(ordered)]
    return StageContract(
        goal=draft.goal, work_items=draft.work_items, inputs=inputs, acceptance=draft.acceptance
    )
