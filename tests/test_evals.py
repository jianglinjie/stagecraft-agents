"""The eval framework, driven by scripted models: it must measure correctly before it measures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest

from stagecraft.agents.executor import ExecutorOutput
from stagecraft.agents.fake_model import FakeModel, reply, submit, tool_call
from stagecraft.agents.planner import PlannerOutput
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.router import RoutingCapsule
from stagecraft.evals import CATEGORIES, CaseLoadError, EvalCase, load_cases, run_case
from stagecraft.evals.checks import value_matches
from stagecraft.evals.cli import main
from stagecraft.evals.judge import JudgeVerdict, ModelJudge
from stagecraft.evals.report import render_comparison, render_report
from stagecraft.evals.runner import SuiteMeta, SuiteResult, run_suite

CASES_DIR = Path(__file__).resolve().parents[1] / "evals" / "cases"
PROMPTS_DIR = CASES_DIR.parent / "prompts"


def fake_models(**scripts: list[Any]) -> dict[str, FakeModel]:
    return {role: FakeModel(scripts.get(role, [])) for role in ROLE_TOOLS}


def case(**fields: Any) -> EvalCase:
    return EvalCase.model_validate({"id": "a-case", "category": "routing", **fields})


def meta(**overrides: Any) -> SuiteMeta:
    values: dict[str, Any] = {
        "label": "test",
        "started_at": "now",
        "model": "fake",
        "judge_model": None,
        "endpoint": "offline",
        "prompts": {},
        "cases": 1,
        "repeat": 1,
        "concurrency": 2,
    }
    return SuiteMeta(**{**values, **overrides})


DIRECT_CASE = {
    "description": "one HTML article",
    "turns": [
        {
            "user": "Turn https://example.com/p/1 into HTML.",
            "expect": {
                "route": "direct",
                "plan": {"exists": False},
                "calls": {
                    "any": {
                        "args": [
                            {"tool": "render_output", "match": {"format": "html"}, "every": True}
                        ]
                    }
                },
                "workspace": {"counts": {"render": {"min": 1}}, "render_formats": ["html"]},
                "output": {"contains_any": ["render_0001"]},
            },
        }
    ],
}


def direct_scripts(final_reply: str = "Done: render_0001.") -> dict[str, list[Any]]:
    return {
        "orchestrator": [
            tool_call("dispatch_router", request="html article"),
            tool_call("fetch_brief", source="https://example.com/p/1"),
            tool_call("write_outline", brief_id="brief_0001"),
            tool_call("write_draft", outline_id="outline_0001"),
            # format omitted on purpose: checks compare arguments after defaults apply
            tool_call("render_output", draft_id="draft_0001"),
            reply(final_reply),
        ],
        "router": [submit("submit_route", RoutingCapsule(route="direct", reason="one piece"))],
    }


# -- loading -------------------------------------------------------------------------------


def test_the_case_set_has_40_to_60_valid_cases_across_four_categories() -> None:
    cases = load_cases(CASES_DIR)

    assert 40 <= len(cases) <= 60
    assert {c.category for c in cases} == set(CATEGORIES)
    assert len({c.id for c in cases}) == len(cases)
    assert any(c.judge for c in cases)


@pytest.mark.parametrize(
    ("calls", "problem"),
    [
        ({"orchestrator": {"excludes": ["plan_write_contract"]}}, "no tool is named"),
        ({"planner": {"includes": ["render_output"]}}, "planner holds no tool"),
        ({"executor": {"args": [{"tool": "dispatch_router", "match": {}}]}}, "holds no tool"),
    ],
)
def test_a_check_naming_a_tool_nobody_could_call_fails_the_load(
    tmp_path: Path, calls: dict[str, Any], problem: str
) -> None:
    (tmp_path / "bad.yaml").write_text(
        json.dumps(
            {
                "category": "robustness",
                "cases": [
                    {
                        "id": "typo",
                        "description": "d",
                        "turns": [{"user": "hi", "expect": {"calls": calls}}],
                    }
                ],
            }
        )
    )
    with pytest.raises(CaseLoadError, match=problem):
        load_cases(tmp_path)


def test_unknown_operators_and_duplicate_ids_fail_the_load(tmp_path: Path) -> None:
    turn = {"user": "hi"}
    bad_operator = {
        "id": "op",
        "description": "d",
        "turns": [
            {
                "user": "hi",
                "expect": {
                    "calls": {
                        "any": {
                            "args": [{"tool": "write_outline", "match": {"sections": {"gt": 3}}}]
                        }
                    }
                },
            }
        ],
    }
    (tmp_path / "a.yaml").write_text(json.dumps({"category": "routing", "cases": [bad_operator]}))
    with pytest.raises(CaseLoadError, match="unknown operator"):
        load_cases(tmp_path)

    same = {"id": "same", "description": "d", "turns": [turn]}
    (tmp_path / "a.yaml").write_text(json.dumps({"category": "routing", "cases": [same]}))
    (tmp_path / "b.yaml").write_text(json.dumps({"category": "robustness", "cases": [same]}))
    with pytest.raises(CaseLoadError, match="duplicate id"):
        load_cases(tmp_path)


def test_the_shipped_orchestrator_prompt_is_a_recorded_version() -> None:
    """A prompt that is not saved under evals/prompts/ cannot be evaluated against another."""
    from stagecraft.agents.orchestrator import ORCHESTRATOR_INSTRUCTIONS

    recorded = {path.name: path.read_text().strip() for path in PROMPTS_DIR.glob("*.md")}
    shipped = ORCHESTRATOR_INSTRUCTIONS.strip()
    assert shipped in recorded.values(), (
        "the orchestrator prompt changed without being recorded: save it under evals/prompts/ "
        "and compare it with the previous version before shipping"
    )


def test_matchers_compare_after_defaults_and_by_operator() -> None:
    assert value_matches({"contains": "P/21"}, "https://example.com/p/21")
    assert value_matches({"contains": "later"}, ["first answer", "Answer later"])
    assert not value_matches({"contains": "x"}, None)
    assert value_matches({"one_of": ["pdf", "html"]}, "pdf")
    assert value_matches({"min": 2}, 3) and not value_matches({"max": 2}, 3)
    assert not value_matches({"min": 0}, True)
    assert value_matches(5, 5) and not value_matches("5", 5)


# -- running ------------------------------------------------------------------------------


async def test_a_direct_case_passes_when_the_agents_do_the_work() -> None:
    result = await run_case(case(**DIRECT_CASE), models=fake_models(**direct_scripts()))

    assert result.status == "passed", [c for c in result.failed_checks]
    labels = [check.label for check in result.checks]
    assert "turn 1: router chooses direct" in labels
    assert "turn 1: any every render_output(format='html')" in labels
    assert labels[-1] == "ids in replies and tool arguments exist"
    assert result.usage.requests == 7  # six orchestrator turns and one router turn
    (turn,) = result.turns
    assert turn.calls["orchestrator"][0] == "dispatch_router"
    assert turn.calls["router"] == ["submit_route"]


async def test_failed_checks_report_what_was_actually_observed() -> None:
    fields = json.loads(json.dumps(DIRECT_CASE))
    fields["turns"][0]["expect"]["route"] = "workflow"
    fields["turns"][0]["expect"]["output"] = {"asks_question": True}

    result = await run_case(case(**fields), models=fake_models(**direct_scripts()))

    assert result.status == "failed"
    details = {check.label: check.detail for check in result.failed_checks}
    assert details["turn 1: router chooses workflow"] == "router chose direct: one piece"
    assert details["turn 1: reply asks the user a question"] == "Done: render_0001."


async def test_invented_ids_fail_while_ids_the_user_typed_are_exempt() -> None:
    scripts = direct_scripts("render_0001 is ready, and so is render_0009. brief_0042 is unknown.")
    fields = json.loads(json.dumps(DIRECT_CASE))
    fields["turns"][0]["user"] = "Turn https://example.com/p/1 into HTML, not brief_0042."

    result = await run_case(case(**fields), models=fake_models(**scripts))

    grounded = result.checks[-1]
    assert not grounded.passed
    assert grounded.detail == "turn 1 reply names render_0009"


async def test_the_approval_loop_drives_a_workflow_to_done() -> None:
    items = [{"id": "w1", "name": "page", "instruction": "one html page"}]
    ref = {"ref_id": "render_0001", "kind": "render", "summary": "page", "work_item_id": "w1"}
    stage = {"plan_id": "plan_0001", "stage_id": "stage_01"}
    scripts = {
        "orchestrator": [
            tool_call("dispatch_router", request="a reviewed page"),
            tool_call("plan_create", objective="a reviewed page"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="a reviewed page"),
            tool_call(
                "plan_update_stage_state", **stage, target="waiting_user", review_kind="plan_review"
            ),
            reply("stage_01 makes the page. Approve?"),
            # the approval loop's turn
            tool_call("plan_update_stage_state", **stage, target="doing", user_confirmed=True),
            tool_call("dispatch_executor", **stage, order=1, goal="page"),
            tool_call("plan_update_stage_state", **stage, target="done"),
            reply("stage_01 is done: render_0001."),
        ],
        "router": [submit("submit_route", RoutingCapsule(route="workflow", reason="review"))],
        "planner": [
            tool_call(
                "plan_write_stage_contract", plan_id="plan_0001", goal="page", work_items=items
            ),
            submit("submit_plan", PlannerOutput(summary="one", questions=[], done_authoring=True)),
        ],
        "executor": [
            tool_call("plan_get_stage_detail", **stage),
            tool_call("fetch_brief", source="https://example.com/p/1"),
            tool_call("write_outline", brief_id="brief_0001"),
            tool_call("write_draft", outline_id="outline_0001"),
            tool_call("render_output", draft_id="draft_0001"),
            tool_call("plan_attach_runtime", **stage, refs=[ref]),
            submit("submit_execution", ExecutorOutput(summary="done", failed_item_ids=[])),
        ],
    }
    workflow = case(
        description="reviewed page",
        turns=[
            {
                "user": "One HTML page for https://example.com/p/1, plan first.",
                "expect": {
                    "route": "workflow",
                    "plan": {"any_in": ["waiting_user"], "none_in": ["doing", "done"]},
                    "calls": {"orchestrator": {"excludes": ["dispatch_executor"]}},
                },
            }
        ],
        approve={"max_turns": 3},
        final={
            "plan": {"all_in": ["done"]},
            "workspace": {"render_formats": ["html"]},
            "calls": {"executor": {"includes": ["plan_attach_runtime"]}},
        },
    )
    models = fake_models(**scripts)

    result = await run_case(workflow, models=models)

    assert result.status == "passed", result.failed_checks
    assert [turn.auto for turn in result.turns] == [False, True]
    assert result.turns[1].user == "Approved. Please continue."
    assert all(model.exhausted for model in models.values())


async def test_a_turn_that_raises_fails_the_case_and_later_turns_are_not_sent() -> None:
    two_turns = case(description="d", turns=[{"user": "first"}, {"user": "second"}])
    models = fake_models(orchestrator=[])  # the script is empty: the first model call raises

    result = await run_case(two_turns, models=models)

    assert result.status == "failed"
    assert result.failed_checks[0].label == "turn 1 completed"
    assert "ScriptExhaustedError" in result.failed_checks[0].detail
    assert len(result.turns) == 1


class DownModel(FakeModel):
    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://llm.invalid"))


async def test_endpoint_failures_are_errors_retried_once_even_inside_a_sub_agent() -> None:
    built = 0

    def models_for(_case: EvalCase) -> dict[str, FakeModel]:
        nonlocal built
        built += 1
        models = fake_models(
            orchestrator=[tool_call("dispatch_router", request="x"), reply("router is down")]
        )
        # The dispatch tool turns the router's failure into a tool_failed result, so the
        # orchestrator carries on. The meter still knows the endpoint failed.
        models["router"] = DownModel([])
        return models

    suite = await run_suite(
        [case(description="d", turns=[{"user": "go"}])], models_for=models_for, meta=meta()
    )

    (result,) = suite.results
    assert result.status == "error"
    assert result.retried and built == 2
    assert result.error is not None and "APIConnectionError" in result.error


class BrokeModel(FakeModel):
    """An endpoint whose account has run out of balance."""

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        request = httpx.Request("POST", "https://llm.invalid/chat/completions")
        raise openai.APIStatusError(
            "Insufficient Balance", response=httpx.Response(402, request=request), body=None
        )


@pytest.mark.parametrize("refusing_role", ["orchestrator", "router"])
def test_a_refused_account_aborts_the_run_instead_of_failing_every_case(
    refusing_role: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []

    def models_for(case: EvalCase) -> dict[str, FakeModel]:
        started.append(case.id)
        models = fake_models(**direct_scripts())
        # The router's refusal is swallowed by dispatch_router and reported as tool_failed;
        # the meter still sees it.
        models[refusing_role] = BrokeModel([])
        return models

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    many = [{"id": f"case-{i}", **DIRECT_CASE} for i in range(6)]
    (cases_dir / "many.yaml").write_text(json.dumps({"category": "routing", "cases": many}))
    out = tmp_path / "out"
    argv = ["--cases", str(cases_dir), "--out", str(out), "--no-judge", "--concurrency", "1"]

    assert main(argv, models_for=models_for) == 3

    assert started == ["case-0"]  # nothing starts after the refusal
    assert not out.exists()
    assert "HTTP 402" in capsys.readouterr().err


# -- judge --------------------------------------------------------------------------------


def verdict(**scores: int) -> JudgeVerdict:
    values = {"addresses_request": 5, "clear_next_step": 5, "honest_status": 5, "concise": 5}
    return JudgeVerdict(**{**values, **scores}, rationale="checked against the state")


async def test_the_judge_grades_after_structure_passes_with_a_fixed_threshold() -> None:
    judged = case(**DIRECT_CASE, judge=True)
    good = FakeModel([submit("submit_verdict", verdict(concise=3))])

    result = await run_case(judged, models=fake_models(**direct_scripts()), judge=ModelJudge(good))

    assert result.status == "passed"
    assert result.judge is not None and result.judge.scores["concise"] == 3
    shown = json.loads(str(good.calls[0].input[0]["content"]))  # type: ignore[index]
    assert shown["conversation"] == [
        {"user": "Turn https://example.com/p/1 into HTML.", "assistant": "Done: render_0001."}
    ]
    assert "render_0001 (html)" in shown["artefacts"]
    assert "honest_status" in (good.calls[0].system_instructions or "")

    harsh = FakeModel([submit("submit_verdict", verdict(honest_status=2))])
    result = await run_case(judged, models=fake_models(**direct_scripts()), judge=ModelJudge(harsh))
    assert result.status == "failed"
    assert result.judge is not None and not result.judge.passed


async def test_the_judge_is_skipped_when_structure_fails_and_an_unusable_judge_is_an_error() -> (
    None
):
    fields = json.loads(json.dumps(DIRECT_CASE))
    fields["turns"][0]["expect"]["route"] = "workflow"
    unused = FakeModel([])
    result = await run_case(
        case(**fields, judge=True), models=fake_models(**direct_scripts()), judge=ModelJudge(unused)
    )
    assert result.status == "failed" and result.judge is None
    assert unused.calls == []

    chatty = FakeModel([reply("Looks great, 5/5.")])
    result = await run_case(
        case(**DIRECT_CASE, judge=True),
        models=fake_models(**direct_scripts()),
        judge=ModelJudge(chatty),
    )
    assert result.status == "error"
    assert result.error is not None and result.error.startswith("judge:")


# -- reports and CLI ----------------------------------------------------------------------


async def test_the_report_gives_rates_by_category_and_explains_each_failure() -> None:
    passing = await run_case(case(**DIRECT_CASE), models=fake_models(**direct_scripts()))
    fields = json.loads(json.dumps(DIRECT_CASE))
    fields["turns"][0]["expect"]["route"] = "workflow"
    failing = await run_case(
        case(**{**fields, "id": "wrong-route", "category": "robustness"}),
        models=fake_models(**direct_scripts()),
    )
    suite = SuiteResult(meta=meta(cases=2), results=[passing, failing])

    report = render_report(suite)

    assert "| routing | 1 | 1 | 0 | 0 | 100% |" in report
    assert "| robustness | 1 | 0 | 1 | 0 | 0% |" in report
    assert "| **all** | 2 | 1 | 1 | 0 | 50% |" in report
    assert "### `wrong-route` (robustness, failed)" in report
    assert (
        "- Failed: turn 1: router chooses workflow. Saw: router chose direct: one piece" in report
    )
    assert "- orchestrator: dispatch_router → fetch_brief → write_outline" in report
    assert '- sent “dispatch_router {"request": "html article"}”' in report

    fixed = passing.model_copy(update={"case_id": "wrong-route", "category": "robustness"})
    after = SuiteResult(meta=meta(label="after", cases=2), results=[passing, fixed])
    comparison = render_comparison(suite, after)
    assert "| robustness | 0/1 (0%) | 1/1 (100%) | +100 pt |" in comparison
    assert "Now passing (1): `wrong-route`" in comparison


def test_the_cli_runs_offline_with_a_prompt_override_and_compares_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "one.yaml").write_text(
        json.dumps({"category": "routing", "cases": [{"id": "direct", **DIRECT_CASE}]})
    )
    prompt = tmp_path / "orchestrator.md"
    prompt.write_text("You are a test orchestrator.\n")
    seen: list[FakeModel] = []

    def models_for(_case: EvalCase) -> dict[str, FakeModel]:
        models = fake_models(**direct_scripts())
        seen.append(models["orchestrator"])
        return models

    out = tmp_path / "out"
    argv = ["--cases", str(cases_dir), "--out", str(out), "--label", "one", "--no-judge"]
    code = main([*argv, "--prompt", f"orchestrator={prompt}"], models_for=models_for)

    assert code == 0
    instructions = seen[0].calls[0].system_instructions or ""
    assert instructions.startswith("You are a test orchestrator.")
    results = json.loads((out / "one.json").read_text())
    assert results["meta"]["prompts"]["orchestrator"].endswith(f"({prompt})")
    assert results["meta"]["prompts"]["router"].endswith("(built-in)")
    assert (out / "one.md").read_text().startswith("# Eval report: one")
    assert "report:" in capsys.readouterr().out

    assert main([*argv, "--prompt", f"orchestrator={prompt}"], models_for=models_for) == 2
    assert "already exists" in capsys.readouterr().err
    assert main([*argv, "--overwrite"], models_for=models_for) == 0
    assert json.loads((out / "one.json").read_text())["meta"]["prompts"]["orchestrator"].endswith(
        "(built-in)"
    )
    capsys.readouterr()

    assert main(["compare", str(out / "one.json"), str(out / "one.json")]) == 0
    assert "| **all** | 1/1 (100%) | 1/1 (100%) | +0 pt |" in capsys.readouterr().out
    assert main(["--cases", str(cases_dir), "--case", "missing"], models_for=models_for) == 2
