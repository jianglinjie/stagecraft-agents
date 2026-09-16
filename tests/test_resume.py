"""Planner memory by task id, and questions that end the turn in code."""

import json
from pathlib import Path
from typing import Any

from stagecraft.agents.fake_model import FakeModel, reply, submit, tool_call
from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.planner import PlannerOutput
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.runtime import AgentRuntime, build_runtime
from stagecraft.plan import PlanStore, ReviewKind, StageState
from stagecraft.tools.fake import FakeWorkspace

ITEMS = [{"id": "w1", "name": "outline", "instruction": "two sections"}]


def make_runtime(
    scripts: dict[str, list[Any]], *, store: PlanStore | None = None, session_db: Path | None = None
) -> tuple[AgentRuntime, dict[str, FakeModel]]:
    models = {role: FakeModel(scripts.get(role, [])) for role in ROLE_TOOLS}
    runtime = build_runtime(
        chat_id="chat_1",
        models=models,  # type: ignore[arg-type]
        store=store,
        workspace=FakeWorkspace(render_delay_seconds=0),
        session_db=session_db,
    )
    return runtime, models


def texts(call_input: Any) -> str:
    return json.dumps(call_input, default=str)


async def test_a_second_dispatch_with_the_same_task_continues_the_planner_conversation() -> None:
    runtime, models = make_runtime(
        {
            "orchestrator": [
                tool_call("plan_create", objective="o"),
                tool_call("dispatch_planner", plan_id="plan_0001", goal="first pass"),
                tool_call(
                    "dispatch_planner", plan_id="plan_0001", goal="second pass", stage_id="stage_01"
                ),
                reply("ok"),
            ],
            "planner": [
                tool_call(
                    "plan_write_stage_contract", plan_id="plan_0001", goal="v1", work_items=ITEMS
                ),
                submit(
                    "submit_plan", PlannerOutput(summary="v1", questions=[], done_authoring=False)
                ),
                submit(
                    "submit_plan", PlannerOutput(summary="v2", questions=[], done_authoring=True)
                ),
            ],
        }
    )
    await run_orchestrator_turn(runtime, "go")

    first_run, _, second_run = models["planner"].calls
    assert "first pass" in texts(first_run.input)
    assert "first pass" in texts(second_run.input)
    assert "second pass" in texts(second_run.input)
    assert '"summary":"v1"' in texts(second_run.input).replace(" ", "").replace('\\"', '"')

    report = models["orchestrator"].calls[2].tool_outputs()[-1]
    assert report["task_id"] == "plan_0001:planner"


async def test_the_planner_conversation_survives_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "agents.db"
    store = PlanStore(tmp_path / "app.db")
    before, _ = make_runtime(
        {
            "orchestrator": [
                tool_call("plan_create", objective="o"),
                tool_call("dispatch_planner", plan_id="plan_0001", goal="remember-me-42"),
                reply("ok"),
            ],
            "planner": [
                submit(
                    "submit_plan", PlannerOutput(summary="s", questions=[], done_authoring=False)
                )
            ],
        },
        store=store,
        session_db=db,
    )
    await run_orchestrator_turn(before, "go")

    after, models = make_runtime(
        {
            "orchestrator": [
                tool_call("dispatch_planner", plan_id="plan_0001", goal="continue"),
                reply("ok"),
            ],
            "planner": [
                submit("submit_plan", PlannerOutput(summary="s", questions=[], done_authoring=True))
            ],
        },
        store=PlanStore(tmp_path / "app.db"),
        session_db=db,
    )
    await run_orchestrator_turn(after, "go on")

    (resumed,) = models["planner"].calls
    assert "remember-me-42" in texts(resumed.input)
    assert "continue" in texts(resumed.input)


async def test_planner_questions_end_the_turn_and_park_the_stage_in_review() -> None:
    runtime, models = make_runtime(
        {
            "orchestrator": [
                tool_call("plan_create", objective="series"),
                tool_call("dispatch_planner", plan_id="plan_0001", goal="series"),
                # never reached: the interrupt ends the run before this turn
                tool_call(
                    "dispatch_executor", plan_id="plan_0001", stage_id="stage_01", order=1, goal="g"
                ),
            ],
            "planner": [
                tool_call(
                    "plan_write_stage_contract", plan_id="plan_0001", goal="draft", work_items=ITEMS
                ),
                submit(
                    "submit_plan",
                    PlannerOutput(
                        summary="need input",
                        questions=["Who is the audience?", "Formal or playful?"],
                        done_authoring=False,
                    ),
                ),
            ],
        }
    )
    result = await run_orchestrator_turn(runtime, "Write a series.")

    assert result.final_output == (
        "Before planning can continue, please answer:\n"
        "1. Who is the audience?\n2. Formal or playful?"
    )
    assert len(models["orchestrator"].calls) == 2
    assert models["executor"].calls == []

    plan = runtime.store.get_plan("plan_0001")
    assert plan.open_questions == ["Who is the audience?", "Formal or playful?"]
    stage = plan.stages[0]
    assert (stage.state, stage.review_kind) == (StageState.WAITING_USER, ReviewKind.PLAN_REVIEW)
    assert stage.questions == plan.open_questions


async def test_answers_clear_the_questions_and_the_rewrite_clears_the_stage() -> None:
    runtime, models = make_runtime(
        {
            "orchestrator": [
                tool_call("plan_create", objective="series"),
                tool_call("dispatch_planner", plan_id="plan_0001", goal="series"),
                tool_call(
                    "dispatch_planner",
                    plan_id="plan_0001",
                    goal="series",
                    answers=["developers", "playful"],
                ),
                reply("Here is the revised stage."),
            ],
            "planner": [
                tool_call(
                    "plan_write_stage_contract", plan_id="plan_0001", goal="draft", work_items=ITEMS
                ),
                submit(
                    "submit_plan",
                    PlannerOutput(summary="?", questions=["Audience?"], done_authoring=False),
                ),
                tool_call(
                    "plan_write_stage_contract",
                    plan_id="plan_0001",
                    stage_id="stage_01",
                    goal="playful draft for developers",
                    work_items=ITEMS,
                ),
                submit(
                    "submit_plan", PlannerOutput(summary="done", questions=[], done_authoring=True)
                ),
            ],
        }
    )
    first = await run_orchestrator_turn(runtime, "Write a series.", remember=True)
    assert "Audience?" in first.final_output

    second = await run_orchestrator_turn(runtime, "developers, playful", remember=True)
    assert second.final_output == "Here is the revised stage."

    plan = runtime.store.get_plan("plan_0001")
    assert plan.open_questions == []
    assert plan.stages[0].questions == []
    assert plan.stages[0].goal == "playful draft for developers"
    assert plan.stages[0].state == StageState.WAITING_USER
    assert all(model.exhausted for model in models.values())
