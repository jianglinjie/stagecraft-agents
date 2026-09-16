"""Tool registry: plain functions in, typed agent tools out.

``@tool`` reads a function's signature (types, defaults, ``Annotated`` descriptions)
and its docstring, and builds one pydantic model for the parameters. That single
model is used twice:

* exported as JSON schema, it is the description the model sees;
* at call time, it validates the arguments the model actually sent.

Because both come from the same definition they cannot drift apart.

:class:`ToolRegistry` holds :class:`ToolSpec` objects by name. A role picks tools by
name with :meth:`ToolRegistry.select`, which adapts them to the SDK's
``FunctionTool``; the role never assembles a tool itself.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from pydantic.fields import FieldInfo

from stagecraft.tools.results import ToolError, ToolResult


class ToolDefinitionError(TypeError):
    """The decorated function cannot be turned into a tool."""


class DuplicateToolError(ValueError):
    """A tool with this name is already registered."""


class UnknownToolError(LookupError):
    """No tool with this name is registered."""


@dataclass(frozen=True)
class ToolSpec:
    """A tool: the function, its parameter model and its result model."""

    name: str
    description: str
    fn: Callable[..., Any]
    params_model: type[BaseModel]
    result_model: type[ToolResult]
    is_async: bool

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ToolSpec:
        tool_name = name or fn.__name__
        doc = description if description is not None else _first_paragraph(inspect.getdoc(fn))
        if not doc:
            raise ToolDefinitionError(
                f"tool {tool_name!r} needs a description: add a docstring or pass description=..."
            )

        hints = get_type_hints(fn, include_extras=True)
        result_model = hints.get("return")
        if not (inspect.isclass(result_model) and issubclass(result_model, ToolResult)):
            raise ToolDefinitionError(
                f"tool {tool_name!r} must declare a ToolResult subclass as its return type"
            )

        fields: dict[str, Any] = {}
        for param in inspect.signature(fn).parameters.values():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                raise ToolDefinitionError(
                    f"tool {tool_name!r}: *args/**kwargs are not allowed on tool functions"
                )
            if param.name not in hints:
                raise ToolDefinitionError(
                    f"tool {tool_name!r}: parameter {param.name!r} needs a type annotation"
                )
            annotation = _field_annotation(hints[param.name])
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[param.name] = (annotation, default)

        params_model = create_model(
            f"{_pascal(tool_name)}Params",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
        return cls(
            name=tool_name,
            description=doc,
            fn=fn,
            params_model=params_model,
            result_model=result_model,
            is_async=inspect.iscoroutinefunction(fn),
        )

    @property
    def params_json_schema(self) -> dict[str, Any]:
        """The parameter schema the model is shown."""
        schema = self.params_model.model_json_schema()
        schema.setdefault("additionalProperties", False)
        return schema

    async def invoke(self, arguments: str | Mapping[str, Any] | None) -> ToolResult:
        """Validate ``arguments`` and run the tool. Never raises for model mistakes."""
        try:
            data = _parse_arguments(arguments)
        except json.JSONDecodeError as err:
            return ToolError(
                code="invalid_arguments",
                message=f"arguments for {self.name} are not valid JSON: {err.msg}",
                hint=f"Send a JSON object matching the {self.name} parameter schema.",
            )

        try:
            params = self.params_model.model_validate(data)
        except ValidationError as err:
            issues = [_format_issue(issue) for issue in err.errors()]
            return ToolError(
                code="invalid_arguments",
                message=f"{self.name} rejected its arguments",
                issues=issues,
                hint=f"Fix the listed fields and call {self.name} again.",
            )

        kwargs = {field: getattr(params, field) for field in self.params_model.model_fields}
        try:
            result = self.fn(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as err:  # noqa: BLE001 - the model must see the failure, not a crash
            return ToolError(
                code="tool_failed",
                message=f"{self.name} failed: {err}",
                hint="Check the ids and inputs you passed; if they look right, tell the user.",
            )

        if not isinstance(result, ToolResult):
            return ToolError(
                code="invalid_result",
                message=f"{self.name} returned {type(result).__name__}, not a ToolResult",
            )
        return result

    def to_function_tool(self, *, strict: bool = False) -> FunctionTool:
        """Adapt this spec to the Agents SDK. The SDK sees a schema and a callback."""
        schema = self.params_json_schema
        if strict:
            schema = ensure_strict_json_schema(schema)

        async def on_invoke_tool(_ctx: ToolContext[Any], input_json: str) -> str:
            result = await self.invoke(input_json)
            return result.model_dump_json()

        return FunctionTool(
            name=self.name,
            description=self.description,
            params_json_schema=schema,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=strict,
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the underlying function directly, bypassing validation."""
        return self.fn(*args, **kwargs)


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    """Turn a function into a :class:`ToolSpec`. Usable bare or with keyword options."""

    def wrap(func: Callable[..., Any]) -> ToolSpec:
        return ToolSpec.from_function(func, name=name, description=description)

    return wrap(fn) if fn is not None else wrap


class ToolRegistry:
    """Tools by name. Registration is explicit; selection is by name only."""

    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise DuplicateToolError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec
        return spec

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Any:
        """``@registry.tool``: build the spec and register it in one step."""

        def wrap(func: Callable[..., Any]) -> ToolSpec:
            return self.register(ToolSpec.from_function(func, name=name, description=description))

        return wrap(fn) if fn is not None else wrap

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise UnknownToolError(f"no tool named {name!r}; known: {self.names()}") from None

    def names(self) -> list[str]:
        return list(self._specs)

    def select(self, *names: str, strict: bool = False) -> list[FunctionTool]:
        """The SDK tools for ``names``, in the order given."""
        return [self.get(name).to_function_tool(strict=strict) for name in names]

    def describe(self) -> list[dict[str, Any]]:
        """Name, description and schema of every tool: the material for an index."""
        return [
            {"name": spec.name, "description": spec.description, "schema": spec.params_json_schema}
            for spec in self._specs.values()
        ]

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)


def _field_annotation(hint: Any) -> Any:
    """Normalise a parameter hint so pydantic sees descriptions and constraints.

    ``Annotated[int, "doc"]`` becomes ``Annotated[int, Field(description="doc")]``;
    ``Field(...)`` metadata is kept as-is, so ``Annotated[int, Field(ge=1)]`` works.
    """
    if get_origin(hint) is not Annotated:
        return hint
    base, *metadata = get_args(hint)
    kept: list[Any] = []
    for item in metadata:
        if isinstance(item, str):
            kept.append(Field(description=item))
        elif isinstance(item, FieldInfo) or item is not None:
            kept.append(item)
    return Annotated[(base, *kept)] if kept else base


def _parse_arguments(arguments: str | Mapping[str, Any] | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("expected a JSON object", text, 0)
        return parsed
    return dict(arguments)


def _format_issue(issue: Mapping[str, Any]) -> str:
    location = ".".join(str(part) for part in issue.get("loc", ())) or "<root>"
    return f"{location}: {issue.get('msg', 'invalid')}"


def _first_paragraph(doc: str | None) -> str:
    if not doc:
        return ""
    return " ".join(doc.strip().split("\n\n", 1)[0].split())


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))
