"""The LangGraph version: same roles, graph-owned control flow, interrupts and checkpoints."""

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from stagecraft.agents.fake_model import FakeModel, reply, tool_call
from stagecraft.graph import GraphDeps, build_graph, resume_thread, start_thread
from stagecraft.tools.fake import FakeWorkspace

ROLES = ("router", "planner", "executor", "direct")


def ref(ref_id: str, kind: str, item: str) -> dict[str, str]:
    return {"ref_id": ref_id, "kind": kind, "summary": ref_id, "work_item_id": item}


def payload(model: FakeModel, call: int) -> dict[str, Any]:
    """The JSON payload a node handed to an agent run."""
    (message,) = model.calls[call].input  # type: ignore[misc]
    return json.loads(message["content"])


def deps_with(**scripts: list[Any]) -> tuple[GraphDeps, dict[str, FakeModel]]:
    models = {role: FakeModel(scripts.get(role, [])) for role in ROLES}
    deps = GraphDeps(models=models, workspace=FakeWorkspace(render_delay_seconds=0))  # type: ignore[arg-type]
    return deps, models


WORKFLOW: dict[str, list[Any]] = {
    "router": [tool_call("submit_route", route="workflow", reason="two dependent parts")],
    "planner": [
        tool_call("submit_graph_plan", summary="?", questions=["Tone?"], stages=[]),
        tool_call(
            "submit_graph_plan",
            summary="two stages",
            questions=[],
            stages=[
                {
                    "goal": "Brief and outline",
                    "work_items": [
                        {"id": "w1", "name": "brief", "instruction": "fetch"},
                        {"id": "w2", "name": "outline", "instruction": "two sections"},
                    ],
                },
                {
                    "goal": "Draft",
                    "inputs": [1],
                    "work_items": [{"id": "w1", "name": "draft", "instruction": "write"}],
                },
            ],
        ),
        tool_call(
            "submit_graph_plan",
            summary="revised",
            questions=[],
            stages=[
                {
                    "goal": "Playful draft",
                    "inputs": [1],
                    "work_items": [{"id": "w1", "name": "draft", "instruction": "playful"}],
                }
            ],
        ),
    ],
    "executor": [
        tool_call("fetch_brief", source="https://example.com/p/1"),
        tool_call(
            "submit_graph_execution",
            summary="half",
            refs=[ref("brief_0001", "brief", "w1")],
            failed_item_ids=["w2"],
        ),
        tool_call("write_outline", brief_id="brief_0001", sections=2),
        tool_call(
            "submit_graph_execution",
            summary="rest",
            refs=[ref("outline_0001", "outline", "w2")],
            failed_item_ids=[],
        ),
        tool_call("write_draft", outline_id="outline_0001", tone="playful"),
        tool_call(
            "submit_graph_execution",
            summary="draft",
            refs=[ref("draft_0001", "draft", "w1")],
            failed_item_ids=[],
        ),
    ],
}


async def test_direct_route_runs_one_node_and_ends() -> None:
    deps, _ = deps_with(
        router=[tool_call("submit_route", route="direct", reason="one piece")],
        direct=[tool_call("fetch_brief", source="x"), reply("brief_0001 ready")],
    )
    graph = build_graph(deps, InMemorySaver())
    turn = await start_thread(graph, "t1", "one brief")

    assert turn.interrupt is None
    assert turn.output == "brief_0001 ready"
    assert turn.plan is None
    assert turn.log == ["route: direct", "direct: done"]


async def test_questions_review_retry_revision_and_finish() -> None:
    deps, models = deps_with(**WORKFLOW)
    graph = build_graph(deps, InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    turn = await start_thread(graph, "t1", "A two-part series.")
    assert turn.interrupt == {"type": "questions", "questions": ["Tone?"]}

    turn = await resume_thread(graph, "t1", ["playful"])
    assert turn.interrupt is not None and turn.interrupt["type"] == "plan_review"
    assert turn.interrupt["stage"]["id"] == "stage_01"
    assert payload(models["planner"], 1)["answers"] == ["playful"]
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("await_review",)
    assert [s["state"] for s in snapshot.values["plan"]["stages"]] == ["waiting_user", "pending"]
    assert snapshot.values["plan"]["stages"][1]["contract"]["inputs"] == ["stage_01"]

    turn = await resume_thread(graph, "t1", {"approved": True})
    assert turn.interrupt is not None and turn.interrupt["stage"]["id"] == "stage_02"
    assert turn.plan is not None
    stage_1 = turn.plan.stage("stage_01")
    assert stage_1 is not None and stage_1.state == "done"
    assert [r.ref_id for r in stage_1.runtime.refs] == ["brief_0001", "outline_0001"]
    assert stage_1.runtime.attempts == 2
    assert payload(models["executor"], 0)["retry_ids"] == []
    assert payload(models["executor"], 2)["retry_ids"] == ["w2"]

    turn = await resume_thread(graph, "t1", {"approved": False, "feedback": "make it playful"})
    assert turn.interrupt is not None and turn.interrupt["stage"]["goal"] == "Playful draft"
    assert payload(models["planner"], 2)["feedback"] == "make it playful"
    assert payload(models["planner"], 2)["revise"]["id"] == "stage_02"

    turn = await resume_thread(graph, "t1", {"approved": True})
    assert turn.interrupt is None
    assert [r["ref_id"] for r in payload(models["executor"], 4)["upstream_refs"]] == [
        "brief_0001",
        "outline_0001",
    ]
    assert turn.output is not None and "[done] stage_02 — Playful draft" in turn.output
    assert turn.log[-1] == "finish"
    assert deps.workspace.ids() == ["brief_0001", "outline_0001", "draft_0001"]
    assert all(models[role].exhausted for role in ("router", "planner", "executor"))


async def test_a_thread_resumes_from_its_checkpoint_after_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "checkpoints.db")
    deps, _ = deps_with(**WORKFLOW)

    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build_graph(deps, saver)
        await start_thread(graph, "t9", "A two-part series.")
        turn = await resume_thread(graph, "t9", ["playful"])
        assert turn.interrupt is not None and turn.interrupt["stage"]["id"] == "stage_01"

    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        restarted = build_graph(deps, saver)
        snapshot = await restarted.aget_state({"configurable": {"thread_id": "t9"}})
        assert snapshot.next == ("await_review",)

        turn = await resume_thread(restarted, "t9", {"approved": True})
        assert turn.interrupt is not None and turn.interrupt["stage"]["id"] == "stage_02"
        assert turn.plan is not None
        stage_1 = turn.plan.stage("stage_01")
        assert stage_1 is not None and stage_1.state == "done"


async def test_rejection_never_executes_and_repeated_failure_blocks() -> None:
    one_stage = {"goal": "Brief", "work_items": [{"id": "w1", "name": "brief", "instruction": "x"}]}
    deps, models = deps_with(
        router=[tool_call("submit_route", route="workflow", reason="review wanted")],
        planner=[
            tool_call("submit_graph_plan", summary="one", questions=[], stages=[one_stage]),
            tool_call("submit_graph_plan", summary="again", questions=[], stages=[one_stage]),
        ],
        executor=[
            tool_call("submit_graph_execution", summary="none", refs=[], failed_item_ids=["w1"]),
            tool_call("submit_graph_execution", summary="none", refs=[], failed_item_ids=["w1"]),
        ],
    )
    graph = build_graph(deps, InMemorySaver())
    await start_thread(graph, "t2", "brief")

    turn = await resume_thread(graph, "t2", {"approved": False, "feedback": "not yet"})
    assert turn.interrupt is not None and turn.interrupt["type"] == "plan_review"
    assert models["executor"].calls == []

    turn = await resume_thread(graph, "t2", {"approved": True})
    assert turn.interrupt is None
    assert turn.plan is not None
    stage = turn.plan.stage("stage_01")
    assert stage is not None and stage.state == "blocked"
    assert stage.blocked_reason == "unfinished after 2 attempts"
    assert "[blocked] stage_01" in (turn.output or "")
