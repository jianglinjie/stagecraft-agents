"""The four roles end to end, each driven by its own scripted model."""

import json
from typing import Any

from stagecraft.agents.executor import ExecutorOutput
from stagecraft.agents.fake_model import FakeModel, RecordedCall, reply, submit, tool_call
from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.planner import PlannerOutput
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.router import RoutingCapsule
from stagecraft.agents.runtime import AgentRuntime, build_runtime
from stagecraft.agents.single import follow_up
from stagecraft.plan import ReviewKind, StageState
from stagecraft.tools import RunContext, ToolError
from stagecraft.tools.fake import FakeWorkspace

S = StageState
MARKER = "private-marker-7f3"

STAGE_1_ITEMS = [
    {"id": "w1", "name": "brief", "instruction": "fetch the product page"},
    {"id": "w2", "name": "outline", "instruction": "two sections"},
]
STAGE_2_ITEMS = [
    {"id": "w1", "name": "draft", "instruction": "playful draft"},
    {"id": "w2", "name": "render", "instruction": "html"},
]


def runtime_with(**scripts: list[Any]) -> tuple[AgentRuntime, dict[str, FakeModel]]:
    models = {role: FakeModel(scripts.get(role, [])) for role in ROLE_TOOLS}
    runtime = build_runtime(
        chat_id="chat_1",
        models=models,  # type: ignore[arg-type]
        workspace=FakeWorkspace(render_delay_seconds=0),
    )
    return runtime, models


def user_messages(call: RecordedCall) -> list[str]:
    if isinstance(call.input, str):
        return [call.input]
    return [
        item["content"]
        for item in call.input
        if isinstance(item, dict) and item.get("role") == "user"
    ]


def last_tool_output(call: RecordedCall) -> dict[str, Any]:
    return call.tool_outputs()[-1]


async def test_router_planner_review_confirm_execute_done_across_two_turns() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call("dispatch_router", request="Two-part series about product 1"),
            tool_call("plan_create", objective="Two-part series about product 1"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="Two-part series"),
            tool_call(
                "plan_update_stage_state",
                plan_id="plan_0001",
                stage_id="stage_01",
                target="waiting_user",
                review_kind="plan_review",
            ),
            reply("Plan: 1) brief and outline 2) draft and render. Approve stage 1?"),
            # second turn, after the user approves
            tool_call(
                "plan_update_stage_state",
                plan_id="plan_0001",
                stage_id="stage_01",
                target="doing",
                user_confirmed=True,
            ),
            tool_call(
                "dispatch_executor",
                plan_id="plan_0001",
                stage_id="stage_01",
                order=1,
                goal="Brief and outline",
            ),
            tool_call(
                "plan_update_stage_state",
                plan_id="plan_0001",
                stage_id="stage_01",
                target="done",
            ),
            reply("Stage 1 done: outline_0001."),
        ],
        router=[
            submit("submit_route", RoutingCapsule(route="workflow", reason="two dependent parts"))
        ],
        planner=[
            tool_call(
                "plan_write_stage_contract",
                plan_id="plan_0001",
                goal="Brief and outline",
                work_items=STAGE_1_ITEMS,
                acceptance="an outline with two sections",
            ),
            tool_call(
                "plan_write_stage_contract",
                plan_id="plan_0001",
                goal="Draft and render",
                inputs=["stage_01"],
                work_items=STAGE_2_ITEMS,
                acceptance="an html render",
                expected_revision=1,
            ),
            submit(
                "submit_plan",
                PlannerOutput(summary="two stages", questions=[], done_authoring=True),
            ),
        ],
        executor=[
            tool_call("plan_get_stage_detail", plan_id="plan_0001", stage_id="stage_01"),
            tool_call("fetch_brief", source="https://example.com/p/1"),
            tool_call("write_outline", brief_id="brief_0001", sections=2),
            tool_call(
                "plan_attach_runtime",
                plan_id="plan_0001",
                stage_id="stage_01",
                refs=[
                    {"ref_id": "brief_0001", "kind": "brief", "summary": "b", "work_item_id": "w1"},
                    {
                        "ref_id": "outline_0001",
                        "kind": "outline",
                        "summary": "o",
                        "work_item_id": "w2",
                    },
                ],
            ),
            submit(
                "submit_execution", ExecutorOutput(summary="brief and outline", failed_item_ids=[])
            ),
        ],
    )

    first = await run_orchestrator_turn(runtime, f"Write a two-part series. {MARKER}")
    assert "Approve stage 1?" in first.final_output

    plan = runtime.store.get_plan("plan_0001")
    stage_1, stage_2 = plan.ordered()
    assert (stage_1.state, stage_1.review_kind) == (S.WAITING_USER, ReviewKind.PLAN_REVIEW)
    assert stage_2.state == S.PENDING
    assert stage_2.contract is not None and stage_2.contract.inputs == ["stage_01"]

    planner_report = last_tool_output(models["orchestrator"].calls[3])
    assert planner_report["authored_stage_ids"] == ["stage_01", "stage_02"]
    assert "plan_review" in planner_report["next_action"]

    second = await run_orchestrator_turn(runtime, follow_up(first, "Approved, go."))
    assert second.final_output == "Stage 1 done: outline_0001."

    plan = runtime.store.get_plan("plan_0001")
    stage_1, stage_2 = plan.ordered()
    assert stage_1.state == S.DONE
    assert [r.ref_id for r in stage_1.runtime.refs] == ["brief_0001", "outline_0001"]
    assert stage_2.state == S.PENDING
    assert runtime.workspace.ids() == ["brief_0001", "outline_0001"]

    executor_report = last_tool_output(models["orchestrator"].calls[7])
    assert executor_report["pending_items"] == []
    assert [r["ref_id"] for r in executor_report["refs"]] == ["brief_0001", "outline_0001"]

    for role, model in models.items():
        assert model.exhausted, f"{role} left {model.remaining_turns} scripted turns unused"


async def test_sub_agents_see_the_payload_and_nothing_else() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call("dispatch_router", request="an article"),
            reply("ok"),
        ],
        router=[submit("submit_route", RoutingCapsule(route="direct", reason="one piece"))],
    )
    await run_orchestrator_turn(runtime, f"Please write an article. {MARKER}")

    (router_call,) = models["router"].calls
    assert user_messages(router_call) == [
        json.dumps({"request": "an article"}, separators=(",", ":"))
    ]
    assert isinstance(router_call.input, list) and len(router_call.input) == 1
    assert MARKER not in json.dumps(router_call.input, default=str)
    assert MARKER in json.dumps(models["orchestrator"].calls[0].input, default=str)


async def test_direct_route_does_the_work_without_a_plan() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call("dispatch_router", request="one short article"),
            tool_call("fetch_brief", source="topic"),
            reply("brief_0001 fetched"),
        ],
        router=[submit("submit_route", RoutingCapsule(route="direct", reason="single piece"))],
    )
    result = await run_orchestrator_turn(runtime, "One short article about topic.")

    assert result.final_output == "brief_0001 fetched"
    route = last_tool_output(models["orchestrator"].calls[1])
    assert route["route"] == "direct"
    assert "content tools" in route["next_action"]
    assert runtime.store.latest_for_chat("chat_1") is None


async def test_dispatch_reports_what_the_store_holds_not_what_the_planner_claims() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call("plan_create", objective="o"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="o"),
            reply("ok"),
        ],
        planner=[
            tool_call(
                "plan_write_stage_contract", plan_id="plan_0001", goal="only one", work_items=[]
            ),
            submit(
                "submit_plan",
                PlannerOutput(summary="wrote three stages", questions=[], done_authoring=True),
            ),
        ],
    )
    await run_orchestrator_turn(runtime, "go")

    report = last_tool_output(models["orchestrator"].calls[2])
    assert report["summary"] == "wrote three stages"
    assert report["authored_stage_ids"] == ["stage_01"]


async def test_executor_cannot_start_before_the_stage_is_confirmed() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call("plan_create", objective="o"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="o"),
            tool_call(
                "dispatch_executor", plan_id="plan_0001", stage_id="stage_01", order=1, goal="g"
            ),
            tool_call(
                "plan_update_stage_state",
                plan_id="plan_0001",
                stage_id="stage_01",
                target="waiting_user",
                review_kind="plan_review",
            ),
            tool_call(
                "plan_update_stage_state",
                plan_id="plan_0001",
                stage_id="stage_01",
                target="doing",
            ),
            reply("waiting for approval"),
        ],
        planner=[
            tool_call(
                "plan_write_stage_contract",
                plan_id="plan_0001",
                goal="g",
                work_items=STAGE_1_ITEMS,
            ),
            submit("submit_plan", PlannerOutput(summary="one", questions=[], done_authoring=True)),
        ],
    )
    await run_orchestrator_turn(runtime, "go")

    refused = last_tool_output(models["orchestrator"].calls[3])
    assert refused["code"] == "illegal_transition"
    assert "user_confirmed" in refused["hint"]
    assert models["executor"].calls == []

    unconfirmed = last_tool_output(models["orchestrator"].calls[5])
    assert unconfirmed["code"] == "confirmation_required"
    _, stage = runtime.store.get_stage("plan_0001", "stage_01")
    assert stage.state == S.WAITING_USER


async def test_an_executor_that_attaches_nothing_cannot_be_marked_done() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call(
                "dispatch_executor", plan_id="plan_0001", stage_id="stage_01", order=1, goal="g"
            ),
            tool_call(
                "plan_update_stage_state", plan_id="plan_0001", stage_id="stage_01", target="done"
            ),
            reply("stuck"),
        ],
        executor=[
            tool_call("plan_get_stage_detail", plan_id="plan_0001", stage_id="stage_01"),
            submit("submit_execution", ExecutorOutput(summary="all done!", failed_item_ids=[])),
        ],
    )
    store = runtime.store
    store.create_plan(chat_id="chat_1", objective="o", role="orchestrator")
    from stagecraft.plan import StageContract, WorkItem

    store.write_stage_contract(
        plan_id="plan_0001",
        role="planner",
        contract=StageContract(goal="g", work_items=[WorkItem(id="w1", name="n", instruction="i")]),
    )
    store.update_stage_state(
        plan_id="plan_0001",
        stage_id="stage_01",
        role="orchestrator",
        target=S.WAITING_USER,
        review_kind=ReviewKind.PLAN_REVIEW,
    )
    store.update_stage_state(
        plan_id="plan_0001",
        stage_id="stage_01",
        role="orchestrator",
        target=S.DOING,
        user_confirmed=True,
    )

    await run_orchestrator_turn(runtime, "run it")

    report = last_tool_output(models["orchestrator"].calls[1])
    assert report["summary"] == "all done!"
    assert report["pending_items"] == ["w1"]
    assert "retry" in report["next_action"]

    refused = last_tool_output(models["orchestrator"].calls[2])
    assert refused["code"] == "results_required"


async def test_sub_agents_hold_no_dispatch_tools_and_cannot_borrow_one() -> None:
    runtime, _ = runtime_with()
    for role in ("router", "planner", "executor"):
        assert not [name for name in ROLE_TOOLS[role] if name.startswith("dispatch_")]
        assert "plan_update_stage_state" not in ROLE_TOOLS[role]

    result = await runtime.registry.get("dispatch_router").invoke(
        {"request": "x"}, RunContext(role="planner", chat_id="chat_1")
    )
    assert isinstance(result, ToolError)
    assert result.code == "role_denied"


async def test_a_failing_sub_agent_becomes_a_tool_error_not_a_crash() -> None:
    runtime, models = runtime_with(
        orchestrator=[tool_call("dispatch_router", request="x"), reply("router is down")],
        router=[],
    )
    result = await run_orchestrator_turn(runtime, "go")

    assert result.final_output == "router is down"
    failure = last_tool_output(models["orchestrator"].calls[1])
    assert failure["code"] == "tool_failed"
    assert "dispatch_router failed" in failure["message"]


async def test_an_invalid_submission_is_returned_for_fixing_and_does_not_end_the_run() -> None:
    runtime, models = runtime_with(
        orchestrator=[tool_call("dispatch_router", request="x"), reply("ok")],
        router=[
            tool_call("submit_route", route="sideways", reason="bad enum"),
            tool_call("submit_route", route="direct", reason="fixed"),
        ],
    )
    await run_orchestrator_turn(runtime, "go")

    rejected = models["router"].calls[1].tool_outputs()[-1]
    assert rejected["code"] == "invalid_arguments"
    assert any(issue.startswith("route:") for issue in rejected["issues"])
    assert last_tool_output(models["orchestrator"].calls[1])["route"] == "direct"
    assert models["router"].exhausted


async def test_a_sub_agent_that_ends_with_text_is_a_failed_dispatch() -> None:
    runtime, models = runtime_with(
        orchestrator=[tool_call("dispatch_router", request="x"), reply("ok")],
        router=[reply("I think this is direct.")],
    )
    await run_orchestrator_turn(runtime, "go")

    failure = last_tool_output(models["orchestrator"].calls[1])
    assert failure["code"] == "tool_failed"
    assert "without a valid submission" in failure["message"]
    assert "I think this is direct." in failure["message"]


async def test_planner_is_told_what_the_executor_can_actually_do() -> None:
    runtime, models = runtime_with(
        orchestrator=[
            tool_call("plan_create", objective="o"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="o"),
            reply("ok"),
        ],
        planner=[
            submit(
                "submit_plan", PlannerOutput(summary="s", questions=["tone?"], done_authoring=False)
            )
        ],
    )
    await run_orchestrator_turn(runtime, "go")

    instructions = models["planner"].calls[0].system_instructions or ""
    for name in ("fetch_brief", "write_outline", "write_draft", "render_output"):
        assert f"- {name}: " in instructions
    assert models["orchestrator"].exhausted is False
    assert len(models["orchestrator"].calls) == 2
