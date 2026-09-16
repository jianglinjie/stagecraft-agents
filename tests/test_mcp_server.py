"""The product over MCP, driven by an in-memory MCP client."""

import threading
from typing import Any

from mcp.client import Client

from stagecraft.agents.fake_model import FakeModel, reply, submit, tool_call, wait_for
from stagecraft.agents.planner import PlannerOutput
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.api.app import Services, build_services
from stagecraft.mcp.server import build_mcp_server

ITEMS = [{"id": "w1", "name": "outline", "instruction": "two sections"}]


def services_with(**scripts: list[Any]) -> Services:
    models = {role: FakeModel(scripts.get(role, [])) for role in ROLE_TOOLS}
    return build_services(
        models_for=lambda _: models,  # type: ignore[return-value, arg-type]
        render_delay=0,
    )


async def test_the_server_offers_user_operations_only() -> None:
    async with Client(build_mcp_server(services_with())) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert set(tools) == {"create_session", "send_message", "get_plan", "get_stage_detail"}
    assert "plan_update_stage_state" not in tools and "fetch_brief" not in tools
    send = tools["send_message"].input_schema
    assert set(send["required"]) == {"session_id", "content"}
    assert "ctx" not in send["properties"]


async def test_send_message_waits_and_forwards_tool_calls_as_progress() -> None:
    services = services_with(
        orchestrator=[
            tool_call("plan_create", objective="series"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="series"),
            tool_call(
                "plan_update_stage_state",
                plan_id="plan_0001",
                stage_id="stage_01",
                target="waiting_user",
                review_kind="plan_review",
            ),
            reply("Stage 1 is an outline. Approve?"),
        ],
        planner=[
            tool_call(
                "plan_write_stage_contract", plan_id="plan_0001", goal="outline", work_items=ITEMS
            ),
            submit("submit_plan", PlannerOutput(summary="one", questions=[], done_authoring=True)),
        ],
    )
    progress: list[tuple[float, str | None]] = []

    async def on_progress(value: float, _total: float | None, message: str | None) -> None:
        progress.append((value, message))

    async with Client(build_mcp_server(services)) as client:
        created = (
            await client.call_tool("create_session", {"topic": "articles"})
        ).structured_content
        assert created is not None and created["topic"] == "articles"
        session_id = created["session_id"]

        result = await client.call_tool(
            "send_message",
            {"session_id": session_id, "content": "A series please.", "client_message_id": "m1"},
            progress_callback=on_progress,
        )
        outcome = result.structured_content
        assert result.is_error is False and outcome is not None
        assert outcome["status"] == "completed"
        assert outcome["output"] == "Stage 1 is an outline. Approve?"
        assert outcome["tool_calls"] == [
            "plan_create",
            "dispatch_planner",
            "plan_update_stage_state",
        ]
        assert "stage_01" in outcome["plan_summary"]
        assert "user_confirmed" in outcome["next_action"]
        assert [message for _, message in progress] == [
            "calling plan_create",
            "calling dispatch_planner",
            "calling plan_update_stage_state",
        ]

        again = await client.call_tool(
            "send_message",
            {"session_id": session_id, "content": "A series please.", "client_message_id": "m1"},
        )
        assert again.structured_content["status"] == "duplicate"  # type: ignore[index]
        assert again.structured_content["turn_id"] == outcome["turn_id"]  # type: ignore[index]

        plan = (await client.call_tool("get_plan", {"session_id": session_id})).structured_content
        assert plan is not None
        assert (plan["next_stage_id"], plan["running"]) == ("stage_01", False)
        assert plan["plan"]["stages"][0]["state"] == "waiting_user"

        detail = await client.call_tool(
            "get_stage_detail", {"session_id": session_id, "stage_id": "stage_01"}
        )
        assert detail.structured_content["contract"]["work_items"][0]["id"] == "w1"  # type: ignore[index]


async def test_errors_come_back_as_tool_errors() -> None:
    release = threading.Event()
    services = services_with(orchestrator=[[wait_for(release), reply("slow")]])
    async with Client(build_mcp_server(services)) as client:
        missing = await client.call_tool("send_message", {"session_id": "nope", "content": "x"})
        assert missing.is_error and "create_session" in missing.content[0].text

        session_id = (await client.call_tool("create_session", {})).structured_content["session_id"]  # type: ignore[index]
        started = await client.call_tool(
            "send_message", {"session_id": session_id, "content": "one", "wait": False}
        )
        assert started.structured_content["status"] == "started"  # type: ignore[index]

        busy = await client.call_tool("send_message", {"session_id": session_id, "content": "two"})
        assert busy.is_error and "already running" in busy.content[0].text

        no_plan = await client.call_tool(
            "get_stage_detail", {"session_id": session_id, "stage_id": "stage_01"}
        )
        assert no_plan.is_error and "no plan" in no_plan.content[0].text
        release.set()
        await services.turns.wait_idle()
