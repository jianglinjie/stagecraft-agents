"""HTTP layer against a real server: 202 + SSE, replay, idempotency, 409, failure."""

import threading
from typing import Any

import httpx

from conftest import read_events, serve, wait_until
from stagecraft.agents.fake_model import FakeModel, reply, tool_call, wait_for
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.api.app import Services, build_services, create_app


def services_with(
    orchestrator: list[Any], **others: list[Any]
) -> tuple[Services, dict[str, FakeModel]]:
    models = {role: FakeModel(others.get(role, [])) for role in ROLE_TOOLS}
    models["orchestrator"] = FakeModel(orchestrator)
    services = build_services(
        models_for=lambda _session_id: models,  # type: ignore[return-value, arg-type]
        render_delay=0,
        heartbeat=0.05,
    )
    return services, models


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in events]


def turn_done(events: list[dict[str, Any]]) -> bool:
    """The turn has ended and the closing snapshot (running=false) has been sent."""
    ended = any(e["event"] in ("turn_completed", "turn_failed") for e in events)
    return ended and events[-1]["event"] == "state" and events[-1]["running"] is False


def test_a_turn_streams_items_state_and_completion_in_order() -> None:
    services, models = services_with(
        [
            tool_call("plan_create", objective="series"),
            tool_call("fetch_brief", source="https://example.com/p/1"),
            reply("Plan created and brief fetched."),
        ]
    )
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        session = http.post("/sessions", json={"title": "demo"}).json()
        sent = http.post(f"/sessions/{session['id']}/messages", json={"content": "Start."})
        assert sent.status_code == 202
        assert sent.json()["status"] == "started"

        events = read_events(base, session["id"], until=turn_done)
        assert [e["id"] for e in events] == list(range(1, len(events) + 1))

        names = kinds(events)
        assert names[:2] == ["turn_started", "state"]
        assert names[-2:] == ["turn_completed", "state"]

        tool_events = [e for e in events if e.get("kind") == "tool_call"]
        assert [(e["event"], e["name"]) for e in tool_events] == [
            ("item_started", "plan_create"),
            ("item_completed", "plan_create"),
            ("item_started", "fetch_brief"),
            ("item_completed", "fetch_brief"),
        ]
        assert tool_events[1]["output"]["plan_id"] == "plan_0001"

        after_plan = names.index("item_completed") + 1
        assert names[after_plan] == "state"
        assert events[after_plan]["plan"]["id"] == "plan_0001"
        assert events[after_plan]["running"] is True

        deltas = "".join(e["delta"] for e in events if e["event"] == "item_delta")
        assert deltas == "Plan created and brief fetched."
        message_done = next(
            e for e in events if e.get("kind") == "message" and e["event"] == "item_completed"
        )
        assert message_done["text"] == deltas
        assert events[-2]["output"] == deltas
        assert events[-1]["running"] is False

        wait_until(lambda: http.get(f"/sessions/{session['id']}").json()["running"] is False)
        snapshot = http.get(f"/sessions/{session['id']}").json()
        assert [m["role"] for m in snapshot["messages"]] == ["user", "assistant"]
        assert snapshot["turns"][0]["status"] == "completed"
        assert snapshot["plan"]["id"] == "plan_0001"
        assert snapshot["last_seq"] == len(events)


def test_reconnecting_with_last_event_id_resumes_without_gaps_or_duplicates() -> None:
    services, _ = services_with([tool_call("fetch_brief", source="x"), reply("done")])
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        http.post(f"/sessions/{session_id}/messages", json={"content": "go"})
        everything = read_events(base, session_id, until=turn_done)

        cut = len(everything) // 2
        resumed = read_events(
            base,
            session_id,
            last_event_id=everything[cut - 1]["id"],
            until=lambda evs: len(evs) == len(everything) - cut,
        )
        assert resumed == everything[cut:]


def test_resending_the_same_client_message_id_starts_nothing() -> None:
    services, models = services_with([reply("hello once")])
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        body = {"content": "hi", "client_message_id": "msg-1"}

        first = http.post(f"/sessions/{session_id}/messages", json=body).json()
        read_events(base, session_id, until=turn_done)
        again = http.post(f"/sessions/{session_id}/messages", json=body)

        assert again.status_code == 202
        assert again.json()["status"] == "duplicate"
        assert again.json()["turn_id"] == first["turn_id"]
        assert len(models["orchestrator"].calls) == 1
        snapshot = http.get(f"/sessions/{session_id}").json()
        assert len(snapshot["turns"]) == 1
        assert [m["role"] for m in snapshot["messages"]] == ["user", "assistant"]


def test_a_second_send_during_a_running_turn_is_409_and_works_afterwards() -> None:
    release = threading.Event()
    services, _ = services_with([[wait_for(release), reply("first")], reply("second")])
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        assert (
            http.post(f"/sessions/{session_id}/messages", json={"content": "one"}).status_code
            == 202
        )
        wait_until(lambda: http.get(f"/sessions/{session_id}").json()["running"])

        blocked = http.post(f"/sessions/{session_id}/messages", json={"content": "two"})
        assert blocked.status_code == 409

        # A resend of the running message is not a conflict: it is the same turn.
        release.set()
        wait_until(lambda: not http.get(f"/sessions/{session_id}").json()["running"])
        later = http.post(f"/sessions/{session_id}/messages", json={"content": "two"})
        assert later.status_code == 202
        wait_until(lambda: len(http.get(f"/sessions/{session_id}").json()["messages"]) == 4)


def test_a_resend_while_its_own_turn_is_still_running_is_not_a_conflict() -> None:
    release = threading.Event()
    services, _ = services_with([[wait_for(release), reply("ok")]])
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        body = {"content": "one", "client_message_id": "same"}
        first = http.post(f"/sessions/{session_id}/messages", json=body).json()
        wait_until(lambda: http.get(f"/sessions/{session_id}").json()["running"])

        resend = http.post(f"/sessions/{session_id}/messages", json=body)
        assert resend.status_code == 202
        assert resend.json() == {**first, "duplicate": True, "status": "duplicate"}
        release.set()


def test_a_failing_turn_reports_turn_failed_and_frees_the_session() -> None:
    services, _ = services_with([])  # the model has nothing to say: the run raises
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        http.post(f"/sessions/{session_id}/messages", json={"content": "go"})
        events = read_events(base, session_id, until=turn_done)

        failed = next(e for e in events if e["event"] == "turn_failed")
        assert failed["code"] == "agent_error"
        wait_until(lambda: not http.get(f"/sessions/{session_id}").json()["running"])
        assert http.get(f"/sessions/{session_id}").json()["turns"][0]["status"] == "failed"
        assert (
            http.post(f"/sessions/{session_id}/messages", json={"content": "again"}).status_code
            == 202
        )


def test_unknown_session_and_bad_bodies() -> None:
    services, _ = services_with([])
    with serve(create_app(services)) as base, httpx.Client(base_url=base) as http:
        assert http.get("/sessions/nope").status_code == 404
        assert http.get("/sessions/nope/events").status_code == 404
        assert http.post("/sessions/nope/messages", json={"content": "x"}).status_code == 404
        session_id = http.post("/sessions").json()["id"]
        assert (
            http.post(f"/sessions/{session_id}/messages", json={"content": ""}).status_code == 422
        )
