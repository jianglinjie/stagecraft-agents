"""Tool registry, result types and the fake tool set."""

from stagecraft.tools.context import Role, RunContext
from stagecraft.tools.registry import (
    DuplicateToolError,
    ToolDefinitionError,
    ToolRegistry,
    ToolSpec,
    UnknownToolError,
    tool,
)
from stagecraft.tools.results import StructuredToolError, ToolError, ToolResult

__all__ = [
    "DuplicateToolError",
    "Role",
    "RunContext",
    "StructuredToolError",
    "ToolDefinitionError",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "UnknownToolError",
    "tool",
]
