"""Tool registry, result types and the fake tool set."""

from stagecraft.tools.registry import (
    DuplicateToolError,
    ToolDefinitionError,
    ToolRegistry,
    ToolSpec,
    UnknownToolError,
    tool,
)
from stagecraft.tools.results import ToolError, ToolResult

__all__ = [
    "DuplicateToolError",
    "ToolDefinitionError",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "UnknownToolError",
    "tool",
]
