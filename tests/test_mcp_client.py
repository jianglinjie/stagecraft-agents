"""External MCP tools admitted into the registry."""

import pytest
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as ServerToolError
from pydantic import BaseModel

from stagecraft.agents.fake_model import FakeModel, reply, tool_call
from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.runtime import build_runtime
from stagecraft.mcp.client import (
    ExternalToolResult,
    McpConfigError,
    McpToolBridge,
    load_server_configs,
)
from stagecraft.tools import ToolError, ToolRegistry


class Forecast(BaseModel):
    city: str
    celsius: int


def weather_server() -> MCPServer:
    server = MCPServer("weather")

    @server.tool()
    def forecast(city: str, days: int = 1) -> Forecast:
        """Forecast for a city."""
        if city == "Atlantis":
            raise ServerToolError("unknown city")
        return Forecast(city=city, celsius=21 + days)

    @server.tool()
    def delete_everything() -> str:
        """A tool nobody reviewed."""
        return "gone"

    return server


def test_config_is_read_from_the_environment_with_secret_expansion() -> None:
    env = {
        "STAGECRAFT_MCP_SERVERS": """[
            {"name": "docs", "url": "https://mcp.example.com/mcp",
             "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}, "allowed_tools": ["search"]},
            {"name": "files", "command": "uvx", "args": ["files-mcp"], "allowed_tools": ["*"]}
        ]""",
        "DOCS_TOKEN": "s3cret",
    }
    docs, files = load_server_configs(env)
    assert docs.headers == {"Authorization": "Bearer s3cret"}
    assert (files.command, files.args, files.allowed_tools) == ("uvx", ["files-mcp"], ["*"])
    assert load_server_configs({}) == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('[{"name": "x", "url": "u", "headers": {"A": "${NOPE}"}, "allowed_tools": []}]', "NOPE"),
        ('[{"name": "x", "url": "u", "command": "c", "allowed_tools": []}]', "exactly one"),
        ('[{"name": "x", "url": "u"}]', "allowed_tools"),
        ('{"name": "x"}', "JSON list"),
    ],
)
def test_bad_configs_are_refused(raw: str, message: str) -> None:
    with pytest.raises((McpConfigError, ValueError), match=message):
        load_server_configs({"STAGECRAFT_MCP_SERVERS": raw})


async def test_only_allowed_tools_are_admitted_and_they_behave_like_local_tools() -> None:
    async with McpToolBridge() as bridge:
        specs = await bridge.attach("weather", Client(weather_server()), ["forecast"])
        assert [s.name for s in specs] == ["weather__forecast"]
        assert bridge.skipped == {"weather": ["delete_everything"]}

        registry = ToolRegistry(specs)
        spec = registry.get("weather__forecast")
        assert spec.description == "[weather] Forecast for a city."
        assert set(spec.params_json_schema["properties"]) == {"city", "days"}
        assert spec.params_json_schema["required"] == ["city"]

        ok = await spec.invoke({"city": "Fuzhou", "days": 3})
        assert isinstance(ok, ExternalToolResult)
        assert ok.structured == {"city": "Fuzhou", "celsius": 24}

        refused = await spec.invoke({"city": "Atlantis"})
        assert isinstance(refused, ToolError)
        assert refused.code == "tool_failed" and "unknown city" in refused.message


async def test_a_role_must_still_name_an_admitted_tool_to_hold_it() -> None:
    async with McpToolBridge() as bridge:
        specs = await bridge.attach("weather", Client(weather_server()), ["forecast"])
        models = {role: FakeModel([]) for role in ROLE_TOOLS}
        models["orchestrator"] = FakeModel(
            [tool_call("weather__forecast", city="Fuzhou"), reply("sunny")]
        )
        runtime = build_runtime(
            chat_id="c",
            models=models,  # type: ignore[arg-type]
            extra_tools=specs,
            extra_role_tools={"orchestrator": ["weather__forecast"]},
        )
        assert "weather__forecast" in [t.name for t in runtime.tools("orchestrator")]
        assert "weather__forecast" not in [t.name for t in runtime.tools("executor")]

        result = await run_orchestrator_turn(runtime, "weather?")
        assert result.final_output == "sunny"
        seen = models["orchestrator"].calls[1].tool_outputs()[-1]
        assert seen["structured"]["celsius"] == 22
