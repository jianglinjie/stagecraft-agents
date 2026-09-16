from typing import Annotated, Literal

import pytest
from agents import FunctionTool
from pydantic import Field

from stagecraft.tools import (
    DuplicateToolError,
    ToolDefinitionError,
    ToolError,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
    tool,
)


class EchoResult(ToolResult):
    echoed: str
    count: int


@tool
def echo(
    text: Annotated[str, "Text to echo back."],
    count: Annotated[int, Field(ge=1, le=5, description="How many times.")] = 1,
    mode: Literal["plain", "loud"] = "plain",
) -> EchoResult:
    """Echo text back a few times.

    This second paragraph is implementation detail and must not leak into the
    description shown to the model.
    """
    out = text.upper() if mode == "loud" else text
    return EchoResult(echoed=out * count, count=count)


def test_schema_is_generated_from_signature_and_docstring() -> None:
    assert echo.name == "echo"
    assert echo.description == "Echo text back a few times."
    assert echo.result_model is EchoResult
    assert echo.is_async is False

    schema = echo.params_json_schema
    props = schema["properties"]
    assert schema["required"] == ["text"]
    assert schema["additionalProperties"] is False
    assert props["text"] == {"type": "string", "description": "Text to echo back.", "title": "Text"}
    assert props["count"]["default"] == 1
    assert props["count"]["minimum"] == 1
    assert props["count"]["maximum"] == 5
    assert props["count"]["description"] == "How many times."
    assert props["mode"]["enum"] == ["plain", "loud"]


def test_definition_requires_annotations_docstring_and_result_model() -> None:
    with pytest.raises(ToolDefinitionError, match="type annotation"):

        @tool
        def no_annotation(text) -> EchoResult:  # type: ignore[no-untyped-def]
            """Has a docstring."""
            raise NotImplementedError

    with pytest.raises(ToolDefinitionError, match="needs a description"):

        @tool
        def no_doc(text: str) -> EchoResult:
            raise NotImplementedError

    with pytest.raises(ToolDefinitionError, match="ToolResult subclass"):

        @tool
        def bad_return(text: str) -> dict[str, str]:
            """Returns a dict, which is not allowed."""
            raise NotImplementedError


def test_registry_rejects_duplicates_and_unknown_names() -> None:
    registry = ToolRegistry([echo])
    assert "echo" in registry
    assert registry.names() == ["echo"]

    with pytest.raises(DuplicateToolError):
        registry.register(echo)
    with pytest.raises(UnknownToolError, match="known: \\['echo'\\]"):
        registry.get("missing")
    with pytest.raises(UnknownToolError):
        registry.select("echo", "missing")


def test_registry_decorator_registers_in_place() -> None:
    registry = ToolRegistry()

    @registry.tool(name="shout")
    def shout(text: Annotated[str, "Text."]) -> EchoResult:
        """Shout the text."""
        return EchoResult(echoed=text.upper(), count=1)

    assert registry.get("shout") is shout
    assert shout("hi").echoed == "HI"


async def test_valid_arguments_run_the_tool() -> None:
    result = await echo.invoke({"text": "ab", "count": 2, "mode": "loud"})
    assert isinstance(result, EchoResult)
    assert result.status == "ok"
    assert result.echoed == "ABAB"

    from_json = await echo.invoke('{"text": "x"}')
    assert isinstance(from_json, EchoResult)
    assert from_json.echoed == "x"


async def test_invalid_arguments_return_a_structured_error() -> None:
    result = await echo.invoke({"text": 123, "count": 9, "extra": True})
    assert isinstance(result, ToolError)
    assert result.status == "error"
    assert result.code == "invalid_arguments"
    assert any(issue.startswith("text:") for issue in result.issues)
    assert any(issue.startswith("count:") for issue in result.issues)
    assert any(issue.startswith("extra:") for issue in result.issues)
    assert result.hint is not None and "echo" in result.hint


async def test_malformed_json_returns_a_structured_error() -> None:
    result = await echo.invoke("{not json")
    assert isinstance(result, ToolError)
    assert result.code == "invalid_arguments"

    not_an_object = await echo.invoke("[1, 2]")
    assert isinstance(not_an_object, ToolError)


async def test_tool_exception_becomes_an_error_result() -> None:
    @tool
    def explode(text: str) -> EchoResult:
        """Always fails."""
        raise LookupError("no such thing")

    result = await explode.invoke({"text": "x"})
    assert isinstance(result, ToolError)
    assert result.code == "tool_failed"
    assert "no such thing" in result.message


async def test_async_tools_are_awaited() -> None:
    @tool
    async def slow_echo(text: str) -> EchoResult:
        """Async echo."""
        return EchoResult(echoed=text, count=1)

    assert slow_echo.is_async is True
    result = await slow_echo.invoke({"text": "z"})
    assert isinstance(result, EchoResult)


async def test_function_tool_adapter_returns_json_for_the_model() -> None:
    registry = ToolRegistry([echo])
    (function_tool,) = registry.select("echo")
    assert isinstance(function_tool, FunctionTool)
    assert function_tool.name == "echo"
    assert function_tool.description == "Echo text back a few times."
    assert function_tool.params_json_schema["required"] == ["text"]
    assert function_tool.strict_json_schema is False

    ok = await function_tool.on_invoke_tool(None, '{"text": "hi"}')  # type: ignore[arg-type]
    assert ok == '{"status":"ok","echoed":"hi","count":1}'

    bad = await function_tool.on_invoke_tool(None, '{"text": 1}')  # type: ignore[arg-type]
    assert '"status":"error"' in bad and '"code":"invalid_arguments"' in bad


def test_strict_schema_is_available_on_request() -> None:
    registry = ToolRegistry([echo])
    (function_tool,) = registry.select("echo", strict=True)
    assert function_tool.strict_json_schema is True
    assert sorted(function_tool.params_json_schema["required"]) == ["count", "mode", "text"]


def test_describe_lists_every_tool_with_its_schema() -> None:
    registry = ToolRegistry([echo])
    (entry,) = registry.describe()
    assert entry["name"] == "echo"
    assert entry["schema"]["properties"]["text"]["description"] == "Text to echo back."
