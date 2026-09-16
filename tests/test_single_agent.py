import pytest

from stagecraft.agents.fake_model import FakeModel, ScriptExhaustedError, reply, tool_call
from stagecraft.agents.single import (
    DEFAULT_INSTRUCTIONS,
    build_single_agent,
    format_trace,
    run_single_agent,
)
from stagecraft.tools.fake import FakeWorkspace, build_fake_registry


async def test_agent_calls_two_tools_then_replies() -> None:
    registry, workspace = build_fake_registry(FakeWorkspace(render_delay_seconds=0))
    model = FakeModel(
        [
            tool_call("fetch_brief", source="https://example.com/p/1"),
            tool_call("write_outline", brief_id="brief_0001", sections=2),
            reply("Outline ready: outline_0001"),
        ]
    )
    agent = build_single_agent(registry, ["fetch_brief", "write_outline"], model=model)

    result = await run_single_agent(agent, "Plan an outline for product 1.")

    assert result.final_output == "Outline ready: outline_0001"
    assert model.exhausted
    assert workspace.ids() == ["brief_0001", "outline_0001"]

    first, second, third = model.calls
    assert first.system_instructions == DEFAULT_INSTRUCTIONS
    assert first.tool_names == ["fetch_brief", "write_outline"]
    assert first.tool_outputs() == []

    (brief_seen,) = second.tool_outputs()
    assert brief_seen["status"] == "ok"
    assert brief_seen["brief_id"] == "brief_0001"
    assert "text" not in brief_seen

    brief_again, outline_seen = third.tool_outputs()
    assert brief_again["brief_id"] == "brief_0001"
    assert outline_seen["outline_id"] == "outline_0001"
    assert outline_seen["section_count"] == 2


async def test_agent_sees_a_structured_error_and_recovers() -> None:
    registry, workspace = build_fake_registry()
    model = FakeModel(
        [
            tool_call("fetch_brief", source="topic"),
            tool_call("write_outline", brief_id="brief_0001", sections=99),
            tool_call("write_outline", brief_id="brief_0001", sections=4),
            reply("done: outline_0001"),
        ]
    )
    agent = build_single_agent(registry, ["fetch_brief", "write_outline"], model=model)

    result = await run_single_agent(agent, "Outline it.")

    assert result.final_output == "done: outline_0001"
    error_seen = model.calls[2].tool_outputs()[-1]
    assert error_seen["status"] == "error"
    assert error_seen["code"] == "invalid_arguments"
    assert any(issue.startswith("sections:") for issue in error_seen["issues"])
    assert workspace.ids("outline") == ["outline_0001"]


async def test_parallel_tool_calls_in_one_turn() -> None:
    registry, workspace = build_fake_registry()
    model = FakeModel(
        [
            [tool_call("fetch_brief", source="a"), tool_call("fetch_brief", source="b")],
            reply("two briefs"),
        ]
    )
    agent = build_single_agent(registry, ["fetch_brief"], model=model)

    result = await run_single_agent(agent, "Fetch both.")

    assert result.final_output == "two briefs"
    assert workspace.ids("brief") == ["brief_0001", "brief_0002"]
    assert len(model.calls[1].tool_outputs()) == 2


async def test_exhausted_script_fails_loudly() -> None:
    registry, _ = build_fake_registry()
    model = FakeModel([tool_call("fetch_brief", source="a")])
    agent = build_single_agent(registry, ["fetch_brief"], model=model)

    with pytest.raises(ScriptExhaustedError):
        await run_single_agent(agent, "Fetch.")
    assert model.remaining_turns == 0


def test_agent_only_holds_the_tools_it_named() -> None:
    registry, _ = build_fake_registry()
    agent = build_single_agent(registry, ["render_output"], model=FakeModel([]))
    assert [t.name for t in agent.tools] == ["render_output"]


async def test_trace_lists_each_call_and_result_in_order() -> None:
    registry, _ = build_fake_registry()
    model = FakeModel([tool_call("fetch_brief", source="a"), reply("ok")])
    agent = build_single_agent(registry, ["fetch_brief"], model=model)

    result = await run_single_agent(agent, "Fetch.")
    call, output = format_trace(result)

    assert call == '-> fetch_brief({"source": "a"})'
    assert output.startswith('<- {"status":"ok","brief_id":"brief_0001"')
