"""Assets and memory through HTTP."""

from typing import Any

import httpx

from conftest import read_events, serve, wait_until
from stagecraft.agents.fake_model import FakeModel, reply
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.api.app import build_services, create_app


def app_with(orchestrator: list[Any], **kwargs: Any):  # type: ignore[no-untyped-def]
    models = {role: FakeModel([]) for role in ROLE_TOOLS}
    models["orchestrator"] = FakeModel(orchestrator)
    services = build_services(
        models_for=lambda _: models,  # type: ignore[return-value, arg-type]
        render_delay=0,
        heartbeat=0.05,
        **kwargs,
    )
    return create_app(services), services, models


def done(events: list[dict[str, Any]]) -> bool:
    return (
        bool(events)
        and events[-1]["event"] == "state"
        and events[-1]["running"] is False
        and any(e["event"] in ("turn_completed", "turn_failed") for e in events)
    )


def test_uploads_and_panel_archives_arrive_with_the_message_in_one_write() -> None:
    app, services, models = app_with([reply("first"), reply("second")])
    with serve(app) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        first = http.post(
            f"/sessions/{session_id}/messages",
            json={
                "content": "Here are the images.",
                "attachments": [
                    {"name": "hero", "kind": "image", "source_id": "upload:h", "summary": "red"},
                    {"name": "logo", "kind": "image", "source_id": "upload:l"},
                ],
            },
        )
        assert first.status_code == 202
        read_events(base, session_id, until=done)

        second = http.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Dropped the hero.", "archive_assets": ["hero"]},
        )
        assert second.status_code == 202
        wait_until(lambda: len(http.get(f"/sessions/{session_id}").json()["messages"]) == 4)

        snapshot = http.get(f"/sessions/{session_id}").json()
        assert [a["name"] for a in snapshot["assets"]] == ["logo"]
        (hero,) = snapshot["archived_assets"]
        assert hero["archive"]["reason"] == "panel"
        assert hero["archive"]["message_id"] == snapshot["messages"][2]["id"]
        assert "Archived by the user" in (models["orchestrator"].calls[1].system_instructions or "")


def test_a_refused_asset_change_stores_nothing_and_frees_the_session() -> None:
    app, services, models = app_with([reply("ok")])
    with serve(app) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions").json()["id"]
        bad = http.post(
            f"/sessions/{session_id}/messages",
            json={
                "content": "upload and archive",
                "client_message_id": "m1",
                "attachments": [{"name": "hero", "kind": "image", "source_id": "h"}],
                "archive_assets": ["never-existed"],
            },
        )
        assert bad.status_code == 422
        assert bad.json()["detail"]["code"] == "not_found"

        snapshot = http.get(f"/sessions/{session_id}").json()
        assert snapshot["messages"] == [] and snapshot["turns"] == [] and snapshot["assets"] == []
        assert snapshot["running"] is False
        assert models["orchestrator"].calls == []

        retry = http.post(
            f"/sessions/{session_id}/messages",
            json={"content": "upload", "client_message_id": "m1"},
        )
        assert retry.status_code == 202 and retry.json()["status"] == "started"


def test_memory_extraction_rewrites_the_topic_profile_from_the_session() -> None:
    async def rewriter(current: str, transcript: str) -> str:
        assert "Readers are developers" in transcript
        return "- audience: developers"

    app, services, _ = app_with([reply("noted")], rewriter=rewriter)
    with serve(app) as base, httpx.Client(base_url=base) as http:
        session_id = http.post("/sessions", json={"topic": "articles"}).json()["id"]
        http.post(f"/sessions/{session_id}/messages", json={"content": "Readers are developers."})
        read_events(base, session_id, until=done)
        wait_until(lambda: not http.get(f"/sessions/{session_id}").json()["running"])

        profile = http.post(f"/sessions/{session_id}/memory").json()
        assert profile["topic"] == "articles"
        assert profile["content"] == "- audience: developers"
        assert services.long_term.get("articles") is not None

        untopiced = http.post("/sessions").json()["id"]
        assert http.post(f"/sessions/{untopiced}/memory").status_code == 422
