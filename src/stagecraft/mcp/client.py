"""External MCP tools, admitted into the registry one reviewed name at a time.

An MCP server can add or change tools whenever its owner deploys. So admission is
**deny by default**: every server config lists ``allowed_tools`` explicitly, and only
those names become tools here (``["*"]`` is an explicit, visible opt-out). Tools are
namespaced ``<server>__<tool>`` so two servers cannot collide with each other or with
local tools, and a role still has to name them to hold them.

The server owns its tools' schemas and validation. The registry shows the schema
verbatim and passes arguments through; a server-side error comes back to the model as
a ``tool_failed`` result, not a crash.

Servers are configured with ``STAGECRAFT_MCP_SERVERS``, a JSON list::

    [{"name": "docs", "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"},
      "allowed_tools": ["search"]},
     {"name": "files", "command": "uvx", "args": ["some-mcp-server"],
      "allowed_tools": ["read_file"]}]

``${VAR}`` in headers and env is read from the environment, so secrets stay out of the
config value itself.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from contextlib import AsyncExitStack
from typing import Any

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from stagecraft.tools.registry import ToolSpec
from stagecraft.tools.results import StructuredToolError, ToolResult

ENV_VAR = "STAGECRAFT_MCP_SERVERS"
TEXT_LIMIT = 4000


class McpConfigError(ValueError):
    pass


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str]
    timeout_seconds: float = 60.0

    @model_validator(mode="after")
    def exactly_one_transport(self) -> McpServerConfig:
        if (self.command is None) == (self.url is None):
            raise ValueError(f"server {self.name!r}: set exactly one of command or url")
        return self


class ExternalToolResult(ToolResult):
    server: str
    tool: str
    text: str
    structured: dict[str, Any] | None = None


def load_server_configs(env: Mapping[str, str] | None = None) -> list[McpServerConfig]:
    source = os.environ if env is None else env
    raw = source.get(ENV_VAR, "").strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as err:
        raise McpConfigError(f"{ENV_VAR} is not valid JSON: {err}") from None
    if not isinstance(entries, list):
        raise McpConfigError(f"{ENV_VAR} must be a JSON list")
    configs = []
    for entry in entries:
        config = McpServerConfig.model_validate(entry)
        configs.append(
            config.model_copy(
                update={
                    "headers": {k: _expand(v, source) for k, v in config.headers.items()},
                    "env": {k: _expand(v, source) for k, v in config.env.items()},
                }
            )
        )
    return configs


class McpToolBridge:
    """Holds MCP client connections open and turns admitted tools into ``ToolSpec``s."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.skipped: dict[str, list[str]] = {}

    async def __aenter__(self) -> McpToolBridge:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def connect(self, config: McpServerConfig) -> list[ToolSpec]:
        if config.command is not None:
            target: Any = StdioServerParameters(
                command=config.command, args=config.args, env=config.env or None
            )
        elif config.headers:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            http = httpx2.AsyncClient(headers=config.headers, timeout=config.timeout_seconds)
            await self._stack.enter_async_context(http)
            target = streamable_http_client(config.url or "", http_client=http)
        else:
            target = config.url
        client = Client(target, read_timeout_seconds=config.timeout_seconds)
        return await self.attach(config.name, client, config.allowed_tools)

    async def attach(self, server: str, client: Client, allowed_tools: list[str]) -> list[ToolSpec]:
        """Open ``client`` for the bridge's lifetime and admit the allowed tools."""
        opened = await self._stack.enter_async_context(client)
        listed = await opened.list_tools()
        admitted, skipped = [], []
        for tool in listed.tools:
            if "*" in allowed_tools or tool.name in allowed_tools:
                admitted.append(_spec(server, opened, tool))
            else:
                skipped.append(tool.name)
        self.skipped[server] = skipped
        return admitted


def _spec(server: str, client: Client, tool: Any) -> ToolSpec:
    qualified = f"{server}__{tool.name}"

    async def call(**arguments: Any) -> ExternalToolResult:
        result = await client.call_tool(tool.name, arguments)
        text = "\n".join(
            part.text for part in result.content if getattr(part, "type", None) == "text"
        )
        if result.is_error:
            raise StructuredToolError(
                f"{qualified} failed: {_clip(text)}",
                code="tool_failed",
                hint="The external server refused the call. Check arguments against its schema.",
            )
        return ExternalToolResult(
            server=server,
            tool=tool.name,
            text=_clip(text),
            structured=result.structured_content,
        )

    schema = dict(tool.input_schema or {"type": "object", "properties": {}})
    schema.setdefault("type", "object")
    return ToolSpec(
        name=qualified,
        description=f"[{server}] {tool.description or tool.name}",
        fn=call,
        params_model=create_model(
            f"{qualified.title().replace('_', '').replace('-', '')}Arguments",
            __config__=ConfigDict(extra="allow"),
        ),
        result_model=ExternalToolResult,
        is_async=True,
        schema_override=schema,
    )


def _expand(value: str, env: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env:
            raise McpConfigError(f"environment variable {name} is referenced but not set")
        return env[name]

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)


def _clip(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"
