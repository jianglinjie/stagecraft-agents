"""Eval cases: YAML in, validated models out.

A case is a short scripted conversation plus what must be true afterwards. Most checks
read the Plan Store, the workspace and the recorded tool calls. Only the ``output``
checks look at the wording of a reply, and the judge grades what structure cannot.

Every name in a case is validated when it loads. An ``excludes`` check naming a tool
that does not exist passes forever, so a typo there has to fail the load instead of
silently passing every run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.plan import StageState

Category = Literal["routing", "tool_calls", "completion", "robustness"]
CATEGORIES: tuple[Category, ...] = ("routing", "tool_calls", "completion", "robustness")
CallScope = Literal["orchestrator", "router", "planner", "executor", "any"]
ArtifactKind = Literal["brief", "outline", "draft", "render"]
MATCH_OPERATORS = frozenset({"contains", "one_of", "min", "max"})


class CaseLoadError(ValueError):
    """A case file that cannot be trusted to measure anything."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Range(_Strict):
    min: int | None = None
    max: int | None = None

    def holds(self, value: int) -> bool:
        return (self.min is None or value >= self.min) and (self.max is None or value <= self.max)

    def describe(self) -> str:
        if self.min is not None and self.max is not None:
            return str(self.min) if self.min == self.max else f"{self.min}..{self.max}"
        if self.min is not None:
            return f">= {self.min}"
        return f"<= {self.max}" if self.max is not None else "any"


class ArgMatch(_Strict):
    """Some call to ``tool`` (every call, with ``every``) has arguments matching ``match``.

    A value is compared for equality, or with one operator: ``{contains: text}`` (substring,
    case-insensitive; any element for a list), ``{one_of: [...]}``, ``{min: n}``, ``{max: n}``.
    Arguments are compared after the tool's defaults are applied.
    """

    tool: str
    match: dict[str, Any]
    every: bool = False

    @model_validator(mode="after")
    def _known_operators(self) -> ArgMatch:
        for key, value in self.match.items():
            if isinstance(value, dict) and len(value) == 1:
                (operator,) = value
                if operator not in MATCH_OPERATORS:
                    raise ValueError(f"{self.tool}.{key}: unknown operator {operator!r}")
        return self


class CallsExpect(_Strict):
    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    ordered: list[str] = Field(default_factory=list)
    counts: dict[str, Range] = Field(default_factory=dict)
    args: list[ArgMatch] = Field(default_factory=list)


class PlanExpect(_Strict):
    exists: bool | None = None
    stages: Range | None = None
    all_in: list[StageState] = Field(default_factory=list)
    any_in: list[StageState] = Field(default_factory=list)
    none_in: list[StageState] = Field(default_factory=list)
    state_counts: dict[StageState, Range] = Field(default_factory=dict)
    open_questions: bool | None = None
    upstream_inputs: bool | None = Field(
        default=None, description="Every stage after the first names an upstream stage."
    )


class WorkspaceExpect(_Strict):
    counts: dict[ArtifactKind, Range] = Field(default_factory=dict)
    render_formats: list[Literal["html", "pdf", "video"]] = Field(default_factory=list)
    draft_tones: list[Literal["neutral", "playful", "formal"]] = Field(default_factory=list)


class OutputExpect(_Strict):
    contains_any: list[str] = Field(default_factory=list)
    contains_all: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    asks_question: bool | None = None


class Expect(_Strict):
    """What must hold. In a turn: that turn's calls and the state after it. In ``final``: the
    whole case's calls and the state at the end."""

    route: Literal["direct", "workflow"] | None = None
    calls: dict[CallScope, CallsExpect] = Field(default_factory=dict)
    plan: PlanExpect | None = None
    workspace: WorkspaceExpect | None = None
    output: OutputExpect | None = None
    interrupted: bool | None = None


class TurnSpec(_Strict):
    user: str = Field(min_length=1)
    expect: Expect = Field(default_factory=Expect)


class ApproveLoop(_Strict):
    """A simulated user who approves whatever is waiting for review, until nothing is.

    It stops when no stage is waiting, when the plan has open questions (it cannot answer
    them), when a turn changes nothing, or after ``max_turns``.
    """

    message: str = "Approved. Please continue."
    max_turns: int = Field(default=6, ge=1, le=12)


class EvalCase(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    category: Category
    description: str = Field(min_length=1)
    turns: list[TurnSpec] = Field(min_length=1)
    approve: ApproveLoop | None = None
    final: Expect | None = None
    judge: bool = False
    grounded_ids: bool = Field(
        default=True,
        description="Every artefact, plan or stage id in a reply or a tool argument exists.",
    )

    @model_validator(mode="after")
    def _names_resolve(self) -> EvalCase:
        problems: list[str] = []
        for where, expect in self.expectations():
            problems.extend(f"{where}: {problem}" for problem in tool_name_problems(expect))
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def expectations(self) -> list[tuple[str, Expect]]:
        found = [(f"turn {i}", turn.expect) for i, turn in enumerate(self.turns, start=1)]
        if self.final is not None:
            found.append(("final", self.final))
        return found


class _CaseFile(_Strict):
    category: Category
    cases: list[dict[str, Any]] = Field(min_length=1)


def all_tool_names() -> set[str]:
    return {name for names in ROLE_TOOLS.values() for name in names}


def tool_name_problems(expect: Expect) -> list[str]:
    """Names a check uses that no role could ever call."""
    everything = all_tool_names()
    problems: list[str] = []
    for scope, calls in expect.calls.items():
        held = everything if scope == "any" else set(ROLE_TOOLS[scope])
        used = [*calls.includes, *calls.ordered, *calls.counts, *(m.tool for m in calls.args)]
        problems.extend(f"{scope} holds no tool {name!r}" for name in used if name not in held)
        # Excluding a tool the role does not hold is allowed: it catches invented calls.
        problems.extend(
            f"no tool is named {name!r}" for name in calls.excludes if name not in everything
        )
    return problems


def load_cases(directory: str | Path) -> list[EvalCase]:
    """Every case in ``directory/*.yaml``, in file then document order. Ids are unique."""
    cases: list[EvalCase] = []
    seen: dict[str, Path] = {}
    files = sorted(Path(directory).glob("*.yaml"))
    if not files:
        raise CaseLoadError(f"no case files in {directory}")
    for path in files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            case_file = _CaseFile.model_validate(raw)
        except (yaml.YAMLError, ValidationError) as err:
            raise CaseLoadError(f"{path}: {err}") from err
        for index, entry in enumerate(case_file.cases, start=1):
            try:
                case = EvalCase.model_validate({"category": case_file.category, **entry})
            except ValidationError as err:
                name = entry.get("id", f"case #{index}")
                raise CaseLoadError(f"{path}: {name}: {err}") from err
            if case.id in seen:
                raise CaseLoadError(f"{path}: duplicate id {case.id!r} (also in {seen[case.id]})")
            seen[case.id] = path
            cases.append(case)
    return cases
