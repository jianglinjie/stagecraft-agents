"""The console's read-only routes, the session list and sub_run events, against a real server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from conftest import read_events, serve, wait_until
from stagecraft.agents.demo_model import demo_models
from stagecraft.agents.fake_model import FakeModel, reply, submit, tool_call
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.router import RoutingCapsule
from stagecraft.api.app import Services, build_services, create_app
from stagecraft.evals.runner import CaseResult, SuiteMeta, SuiteResult
from stagecraft.tools.retrieval import ReferenceIndex

CORPUS = Path(__file__).resolve().parents[1] / "docs" / "corpus"


def turn_done(events: list[dict[str, Any]]) -> bool:
    ended = any(e["event"] in ("turn_completed", "turn_failed") for e in events)
    return ended and events[-1]["event"] == "state" and events[-1]["running"] is False


def services(**kwargs: Any) -> Services:
    kwargs.setdefault("models_for", lambda _: demo_models())
    return build_services(
        render_delay=0,
        heartbeat=0.05,
        references=ReferenceIndex(corpus_dir=CORPUS),
        **kwargs,
    )


def test_sessions_are_listed_newest_first() -> None:
    with serve(create_app(services())) as base, httpx.Client(base_url=base) as http:
        first = http.post("/sessions", json={"title": "one"}).json()
        second = http.post("/sessions", json={"title": "two", "topic": "launch"}).json()

        listed = http.get("/sessions").json()

        assert [s["id"] for s in listed] == [second["id"], first["id"]]
        assert listed[0] == {**second, "running": False}
        assert http.get("/sessions", params={"limit": 1}).json() == listed[:1]


def test_a_dispatch_is_followed_by_a_sub_run_event_with_payload_and_calls() -> None:
    models = {role: FakeModel([]) for role in ROLE_TOOLS}
    models["orchestrator"] = FakeModel(
        [tool_call("dispatch_router", request="Two posts, reviewed"), reply("It is a workflow.")]
    )
    models["router"] = FakeModel(
        [submit("submit_route", RoutingCapsule(route="workflow", reason="two posts"))]
    )
    app = create_app(services(models_for=lambda _: models))
    with serve(app) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        http.post(f"/sessions/{session_id}/messages", json={"content": "Two posts, reviewed"})
        events = read_events(base, session_id, until=turn_done)

    names = [(e["event"], e.get("name") or e.get("role")) for e in events]
    sub_run = names.index(("sub_run", "router"))
    assert names[sub_run - 1] == ("item_started", "dispatch_router")
    assert names[sub_run + 1] == ("item_completed", "dispatch_router")
    event = events[sub_run]
    assert event["payload"] == {"request": "Two posts, reviewed"}
    (call,) = event["calls"]
    assert call["name"] == "submit_route"
    assert call["arguments"] == {"route": "workflow", "reason": "two posts"}
    assert call["status"] == "ok"
    assert call["output"]["route"] == "workflow"


def test_the_demo_rules_serve_a_reviewed_workflow_over_http() -> None:
    with serve(create_app(services())) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        http.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Write a two-part series about https://example.com/p/1 for managers"},
        )
        events = read_events(base, session_id, until=turn_done)

        roles = [e["role"] for e in events if e["event"] == "sub_run"]
        assert roles == ["router", "planner"]
        planner = next(e for e in events if e.get("role") == "planner")
        assert [c["name"] for c in planner["calls"]][:2] == [
            "search_references",
            "plan_write_stage_contract",
        ]
        final_plan = events[-1]["plan"]
        assert [s["state"] for s in final_plan["stages"]] == ["waiting_user", "pending", "pending"]
        assert final_plan["stages"][0]["contract"]["sources"]

        wait_until(lambda: not http.get(f"/sessions/{session_id}").json()["running"])
        context = http.get(f"/console/sessions/{session_id}/context").json()
        assert context["turn_context"].startswith("## Turn Context")
        assert "[waiting_user] stage_01" in context["turn_context"]
        assert context["memory"]["items"] > 0
        assert context["memory"]["estimated_tokens"] > 0
        assert context["memory"]["threshold_tokens"] == 8000
        assert context["memory"]["recent"][-1]["kind"] == "assistant"
        assert context["profile"] is None
        assert http.get("/console/sessions/nope/context").status_code == 404


def test_info_tools_and_references() -> None:
    app = create_app(services(model_name="demo", memory_threshold_tokens=500))
    with serve(app) as base, httpx.Client(base_url=base) as http:
        info = http.get("/console/info").json()
        assert info["model"] == "demo"
        assert info["memory_threshold_tokens"] == 500
        assert info["references"] == {"chunks": 48, "mode": "bm25_only", "vector_error": None}

        catalog = http.get("/console/tools").json()
        assert catalog["roles"] == {role: list(names) for role, names in ROLE_TOOLS.items()}
        tools = {tool["name"]: tool for tool in catalog["tools"]}
        assert set(tools) >= {name for names in ROLE_TOOLS.values() for name in names}
        assert tools["plan_get_stage_detail"]["roles"] == ["orchestrator", "planner", "executor"]
        write_draft = tools["write_draft"]["parameters"]
        assert set(write_draft["properties"]) == {"outline_id", "tone", "reference_assets"}
        assert "ctx" not in json.dumps(catalog)

        found = http.get("/console/references", params={"q": "PDF datasheet", "top_k": 2}).json()
        assert found["mode"] == "bm25_only"
        assert len(found["hits"]) == 2
        assert found["hits"][0]["pointer"].startswith("format-pdf-datasheet#")
        assert set(found["hits"][0]) == {"pointer", "title", "summary", "score", "matched_by"}
        assert http.get("/console/references").json()["hits"] == []
        assert http.get("/console/references", params={"top_k": 9}).status_code == 422


def test_eval_results_on_disk_are_listed_and_read(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    suite = SuiteResult(
        meta=SuiteMeta(
            label="baseline",
            started_at="2026-09-17 10:00 UTC",
            model="m",
            judge_model=None,
            endpoint="offline",
            prompts={},
            cases=2,
            repeat=1,
            concurrency=1,
        ),
        results=[
            CaseResult(case_id="a", category="routing", description="a", status="passed"),
            CaseResult(case_id="b", category="routing", description="b", status="error"),
        ],
    )
    (local / "baseline.json").write_text(suite.model_dump_json(), encoding="utf-8")
    (local / "baseline.md").write_text("# Eval report: baseline\n", encoding="utf-8")
    (local / "notes.json").write_text('{"not": "results"}', encoding="utf-8")

    app = create_app(services(eval_dirs={"local": local, "docs": tmp_path / "missing"}))
    with serve(app) as base, httpx.Client(base_url=base) as http:
        listing = http.get("/console/evals").json()
        (report,) = listing["reports"]
        assert report["label"] == "baseline"
        assert (report["runs"], report["passed"], report["failed"], report["errors"]) == (
            2,
            1,
            0,
            1,
        )

        detail = http.get("/console/evals/local/baseline").json()
        assert detail["suite"]["meta"]["label"] == "baseline"
        assert [r["status"] for r in detail["suite"]["results"]] == ["passed", "error"]
        assert detail["markdown"] == "# Eval report: baseline\n"

        assert http.get("/console/evals/local/notes").status_code == 404
        assert http.get("/console/evals/local/..baseline").status_code == 404
        assert http.get("/console/evals/elsewhere/baseline").status_code == 404
