"""The offline demo rules, run through the real runtime: every mechanism, no endpoint."""

from __future__ import annotations

from pathlib import Path

from stagecraft.agents.demo_model import (
    DemoCompactor,
    DemoRewriter,
    View,
    demo_models,
    parse_plan,
)
from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.runtime import AgentRuntime, build_runtime
from stagecraft.assets import NewAsset
from stagecraft.plan import StageState
from stagecraft.tools.fake import FakeWorkspace
from stagecraft.tools.retrieval import ReferenceIndex

CORPUS = Path(__file__).resolve().parents[1] / "docs" / "corpus"
S = StageState


def demo_runtime() -> AgentRuntime:
    return build_runtime(
        chat_id="demo",
        models=demo_models(),
        workspace=FakeWorkspace(render_delay_seconds=0),
        references=ReferenceIndex(corpus_dir=CORPUS),
    )


async def say(runtime: AgentRuntime, text: str) -> str:
    result = await run_orchestrator_turn(runtime, text, remember=True)
    return str(result.final_output)


def states(runtime: AgentRuntime) -> list[str]:
    plan = runtime.store.latest_for_chat("demo")
    return [] if plan is None else [stage.state.value for stage in plan.ordered()]


async def test_a_single_piece_takes_the_direct_route_with_the_asked_tone_and_format() -> None:
    runtime = demo_runtime()
    answer = await say(
        runtime, "Write one short playful article about https://example.com/p/1 as a PDF"
    )

    assert answer == "Done: brief_0001 → outline_0001 → draft_0001 → render_0001 (pdf)."
    assert runtime.store.latest_for_chat("demo") is None
    records = runtime.workspace.snapshot()
    assert records["brief_0001"]["source"] == "https://example.com/p/1"
    assert records["draft_0001"]["tone"] == "playful"


async def test_a_series_asks_plans_with_cited_sources_and_runs_only_after_each_approval() -> None:
    runtime = demo_runtime()

    asked = await say(runtime, "Write a two-part series about https://example.com/p/1")
    assert "Who is this for" in asked
    assert "Who is this for" in await say(runtime, "Approved. Please continue.")
    plan = runtime.store.latest_for_chat("demo")
    assert plan is not None and plan.open_questions and not plan.stages

    presented = await say(runtime, "developers, playful tone, as a PDF")
    assert "Stage 1 of 3 (stage_01) needs your review" in presented
    assert states(runtime) == ["waiting_user", "pending", "pending"]
    plan = runtime.store.latest_for_chat("demo")
    assert plan is not None and not plan.open_questions
    for stage in plan.ordered():
        assert stage.contract is not None
        assert stage.contract.sources, stage.id
        for pointer in stage.contract.sources:
            assert await runtime.references.has(pointer)
    assert plan.ordered()[1].contract.inputs == ["stage_01"]  # type: ignore[union-attr]

    held = await say(runtime, "What does the outline cover?")
    assert "waiting for your review" in held
    assert states(runtime) == ["waiting_user", "pending", "pending"]
    assert not runtime.workspace.snapshot()

    await say(runtime, "Approved. Please continue.")
    assert states(runtime) == ["done", "waiting_user", "pending"]
    await say(runtime, "approve")
    finished = await say(runtime, "approve")

    assert finished == "stage_03 produced render_0001. All stages are finished."
    assert states(runtime) == ["done", "done", "done"]
    records = runtime.workspace.snapshot()
    assert records["draft_0001"]["tone"] == "playful"
    assert records["render_0001"]["format"] == "pdf"


async def test_skipping_a_stage_its_successor_needs_ends_in_blocked_with_a_reason() -> None:
    runtime = demo_runtime()
    await say(runtime, "Write a series about our product for beginners")
    await say(runtime, "skip")
    assert states(runtime) == ["omitted", "waiting_user", "pending"]

    blocked = await say(runtime, "approve")

    assert blocked == "stage_02 is blocked: work items w1 did not finish after a retry."
    stage = runtime.store.latest_for_chat("demo").ordered()[1]  # type: ignore[union-attr]
    assert stage.state == S.BLOCKED
    assert stage.runtime.refs == []


async def test_archiving_by_chat_goes_through_the_asset_tool() -> None:
    runtime = demo_runtime()
    assert runtime.assets is not None
    runtime.assets.register("demo", NewAsset(name="logo", kind="image", source_id="upload:l"))

    answer = await say(runtime, "Please archive logo")

    assert answer.startswith("Archived logo.")
    assert runtime.assets.active("demo") == []


def test_the_plan_is_read_from_the_turn_context_summary() -> None:
    summary = (
        "plan plan_0003 (rev 7) — Write a series\n"
        "  1. [done] stage_01 — Brief and outline\n"
        "  2. [waiting_user] stage_02 — A playful draft\n"
        "  open questions: Who reads it? | Which format?"
    )
    plan = parse_plan(summary)

    assert plan is not None
    assert (plan.id, plan.objective) == ("plan_0003", "Write a series")
    assert plan.first("waiting_user").id == "stage_02"  # type: ignore[union-attr]
    assert plan.questions == ["Who reads it?", "Which format?"]
    assert parse_plan("plan plan_0001 (rev 0): no stages yet").stages == []  # type: ignore[union-attr]
    assert parse_plan("No plan yet.") is None


def test_results_are_only_those_since_the_latest_request() -> None:
    view = View.of(
        "",
        [
            {"role": "user", "content": "first"},
            {"type": "function_call", "call_id": "a", "name": "fetch_brief", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "a", "output": '{"status": "ok"}'},
            {"role": "user", "content": "second"},
            {"type": "function_call", "call_id": "b", "name": "write_outline", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "b", "output": '{"status": "error"}'},
        ],
    )

    assert view.request == "second"
    assert [(r.name, r.failed) for r in view.results()] == [("write_outline", True)]


async def test_the_rewriter_replaces_a_preference_instead_of_adding_a_second_one() -> None:
    rewrite = DemoRewriter()
    first = await rewrite("", "user: for developers, playful please\nassistant: ok")
    second = await rewrite(first, "user: make it formal this time\ntool result: playful")

    assert first == "- Audience: developers\n- Tone: playful"
    assert second == "- Audience: developers\n- Tone: formal"


async def test_the_compactor_keeps_requests_and_ids() -> None:
    summary = await DemoCompactor()(
        [
            {"role": "user", "content": "Write about https://example.com/p/1"},
            {
                "type": "function_call_output",
                "call_id": "x",
                "output": '{"brief_id": "brief_0001"}',
            },
        ]
    )
    assert summary == "The user asked: Write about https://example.com/p/1. Ids so far: brief_0001."
