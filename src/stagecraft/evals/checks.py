"""Structured checks: each one reads recorded facts and says what it saw.

A failed check carries the observation, not just "expected X": the report has to say
what the agent actually did, or nobody can tell a wrong expectation from a wrong agent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from stagecraft.evals.cases import (
    ArgMatch,
    CallsExpect,
    Expect,
    OutputExpect,
    PlanExpect,
    Range,
    WorkspaceExpect,
)
from stagecraft.evals.trace import ToolCallRecord, TurnRecord
from stagecraft.plan import Plan

ID_PATTERN = re.compile(r"\b(?:(?:brief|outline|draft|render|plan)_\d{4}|stage_\d{2})\b")
_MISSING = object()


class CheckResult(BaseModel):
    label: str
    passed: bool
    detail: str = ""


@dataclass
class Observation:
    """The facts a set of checks may read: some turns' calls, and the state after the last."""

    turns: Sequence[TurnRecord]

    @property
    def calls(self) -> list[ToolCallRecord]:
        return [call for turn in self.turns for call in turn.calls]

    @property
    def last(self) -> TurnRecord:
        return self.turns[-1]


def evaluate(expect: Expect, observation: Observation) -> list[CheckResult]:
    results: list[CheckResult] = []
    if expect.route is not None:
        results.append(_route(expect.route, observation.calls))
    for scope, calls in expect.calls.items():
        results.extend(_calls(scope, calls, observation.calls))
    if expect.plan is not None:
        results.extend(_plan(expect.plan, observation.last.plan))
    if expect.workspace is not None:
        results.extend(_workspace(expect.workspace, observation.last.workspace))
    if expect.output is not None:
        results.extend(_output(expect.output, observation.last.output or ""))
    if expect.interrupted is not None:
        seen = observation.last.interrupted
        results.append(
            CheckResult(
                label=f"turn interrupted is {str(expect.interrupted).lower()}",
                passed=seen == expect.interrupted,
                detail=f"interrupted: {str(seen).lower()}",
            )
        )
    return results


def grounded_ids(turns: Sequence[TurnRecord], plans: Sequence[Plan]) -> CheckResult:
    """No reply and no tool argument names an id the system never issued.

    Ids the user typed are exempt: repeating the user's own (possibly wrong) id is not an
    invention.
    """
    known: set[str] = set()
    for turn in turns:
        known.update(turn.workspace)
    for plan in plans:
        known.add(plan.id)
        known.update(stage.id for stage in plan.stages)
    from_user = {match for turn in turns for match in ID_PATTERN.findall(turn.user)}

    invented: list[str] = []
    for turn in turns:
        for found in ID_PATTERN.findall(turn.output or ""):
            if found not in known and found not in from_user:
                invented.append(f"turn {turn.index} reply names {found}")
        for call in turn.calls:
            for found in ID_PATTERN.findall(_flatten(call.arguments)):
                if found not in known and found not in from_user:
                    invented.append(f"turn {turn.index} {call.role} passed {found} to {call.name}")
    unique = list(dict.fromkeys(invented))
    return CheckResult(
        label="ids in replies and tool arguments exist",
        passed=not unique,
        detail="; ".join(unique[:6]) if unique else f"{len(known)} known ids",
    )


# -- route -------------------------------------------------------------------------------


def _route(expected: str, calls: Sequence[ToolCallRecord]) -> CheckResult:
    label = f"router chooses {expected}"
    routed = [c for c in calls if c.role == "orchestrator" and c.name == "dispatch_router"]
    if not routed:
        return CheckResult(label=label, passed=False, detail="dispatch_router was not called")
    last = routed[-1]
    output = last.output or {}
    if output.get("status") != "ok":
        return CheckResult(
            label=label, passed=False, detail=f"router failed: {output.get('message', output)}"
        )
    chosen = output.get("route")
    return CheckResult(
        label=label,
        passed=chosen == expected,
        detail=f"router chose {chosen}: {output.get('reason', '')}",
    )


# -- calls -------------------------------------------------------------------------------


def _calls(scope: str, spec: CallsExpect, calls: Sequence[ToolCallRecord]) -> list[CheckResult]:
    mine = [c for c in calls if scope == "any" or c.role == scope]
    names = [c.name for c in mine]
    seen = ", ".join(c.label() for c in mine) or "no calls"
    results: list[CheckResult] = []
    for name in spec.includes:
        results.append(
            CheckResult(label=f"{scope} calls {name}", passed=name in names, detail=seen)
        )
    for name in spec.excludes:
        count = names.count(name)
        results.append(
            CheckResult(
                label=f"{scope} never calls {name}",
                passed=count == 0,
                detail=f"called {count} time(s): {seen}" if count else "not called",
            )
        )
    if spec.ordered:
        results.append(
            CheckResult(
                label=f"{scope} calls in order: {' -> '.join(spec.ordered)}",
                passed=_is_subsequence(spec.ordered, names),
                detail=seen,
            )
        )
    for name, allowed in spec.counts.items():
        count = names.count(name)
        results.append(
            CheckResult(
                label=f"{scope} calls {name} {allowed.describe()} time(s)",
                passed=allowed.holds(count),
                detail=f"called {count} time(s)",
            )
        )
    for match in spec.args:
        results.append(_args(scope, match, mine))
    return results


def _args(scope: str, spec: ArgMatch, calls: Sequence[ToolCallRecord]) -> CheckResult:
    wanted = ", ".join(f"{key}={_describe(value)}" for key, value in spec.match.items())
    label = f"{scope} {'every' if spec.every else 'some'} {spec.tool}({wanted})"
    candidates = [c for c in calls if c.name == spec.tool]
    if not candidates:
        return CheckResult(label=label, passed=False, detail=f"{spec.tool} was not called")
    matched = [c for c in candidates if matches(spec.match, c.arguments)]
    passed = len(matched) == len(candidates) if spec.every else bool(matched)
    shown = "; ".join(
        ", ".join(f"{key}={_short(call.arguments.get(key, '<missing>'))}" for key in spec.match)
        for call in candidates[:4]
    )
    return CheckResult(label=label, passed=passed, detail=f"calls: {shown}")


def matches(expected: Mapping[str, Any], arguments: Mapping[str, Any]) -> bool:
    return all(
        value_matches(value, arguments.get(key, _MISSING)) for key, value in expected.items()
    )


def value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and len(expected) == 1:
        (operator, operand) = next(iter(expected.items()))
        if actual is _MISSING:
            return False
        if operator == "contains":
            needle = str(operand).lower()
            items = actual if isinstance(actual, list) else [actual]
            return any(needle in _flatten(item).lower() for item in items)
        if operator == "one_of":
            return actual in operand
        if operator in ("min", "max"):
            if isinstance(actual, bool) or not isinstance(actual, int | float):
                return False
            return actual >= operand if operator == "min" else actual <= operand
    return actual == expected


# -- plan --------------------------------------------------------------------------------


def _plan(spec: PlanExpect, plan: Plan | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    if spec.exists is not None:
        results.append(
            CheckResult(
                label="a plan exists" if spec.exists else "no plan is created",
                passed=(plan is not None) == spec.exists,
                detail=_plan_detail(plan),
            )
        )
    # No plan reads as a plan with no stages: "no stage is doing" holds, "every stage is
    # done" does not.
    stages = plan.ordered() if plan is not None else []
    questions = plan.open_questions if plan is not None else []
    states = ", ".join(f"{s.id}={s.state}" for s in stages) or (
        "no stages" if plan is not None else "no plan"
    )
    if spec.stages is not None:
        results.append(
            CheckResult(
                label=f"plan has {spec.stages.describe()} stage(s)",
                passed=spec.stages.holds(len(stages)),
                detail=f"{len(stages)} stage(s): {states}",
            )
        )
    if spec.all_in:
        offending = [s.id for s in stages if s.state not in spec.all_in]
        results.append(
            CheckResult(
                label=f"every stage in {_states(spec.all_in)}",
                passed=bool(stages) and not offending,
                detail=states,
            )
        )
    if spec.any_in:
        results.append(
            CheckResult(
                label=f"some stage in {_states(spec.any_in)}",
                passed=any(s.state in spec.any_in for s in stages),
                detail=states,
            )
        )
    if spec.none_in:
        results.append(
            CheckResult(
                label=f"no stage in {_states(spec.none_in)}",
                passed=not any(s.state in spec.none_in for s in stages),
                detail=states,
            )
        )
    for state, allowed in spec.state_counts.items():
        count = sum(1 for s in stages if s.state == state)
        results.append(
            CheckResult(
                label=f"{allowed.describe()} stage(s) in {state}",
                passed=allowed.holds(count),
                detail=states,
            )
        )
    if spec.open_questions is not None:
        results.append(
            CheckResult(
                label="plan has open questions" if spec.open_questions else "no open questions",
                passed=bool(questions) == spec.open_questions,
                detail=" | ".join(questions) or "none",
            )
        )
    if spec.upstream_inputs is not None:
        lacking = [s.id for s in stages[1:] if s.contract is None or not s.contract.inputs]
        results.append(
            CheckResult(
                label="every later stage names an upstream stage",
                passed=bool(stages) and not lacking,
                detail=f"no inputs: {', '.join(lacking)}" if lacking else states,
            )
        )
    return results


# -- workspace and output ----------------------------------------------------------------


def _workspace(
    spec: WorkspaceExpect, records: Mapping[str, Mapping[str, Any]]
) -> list[CheckResult]:
    by_kind: dict[str, list[str]] = {}
    for record_id, record in records.items():
        by_kind.setdefault(str(record.get("kind")), []).append(record_id)
    results: list[CheckResult] = []
    for kind, allowed in spec.counts.items():
        ids = by_kind.get(kind, [])
        results.append(
            CheckResult(
                label=f"{allowed.describe()} {kind}(s) produced",
                passed=allowed.holds(len(ids)),
                detail=f"{len(ids)}: {', '.join(ids)}" if ids else "none",
            )
        )
    results.extend(
        _attribute_check(records, by_kind, "render", "format", spec.render_formats),
    )
    results.extend(_attribute_check(records, by_kind, "draft", "tone", spec.draft_tones))
    return results


def _attribute_check(
    records: Mapping[str, Mapping[str, Any]],
    by_kind: Mapping[str, list[str]],
    kind: str,
    attribute: str,
    allowed: Sequence[str],
) -> Iterable[CheckResult]:
    if not allowed:
        return []
    ids = by_kind.get(kind, [])
    seen = [f"{rid}={records[rid].get(attribute)}" for rid in ids]
    return [
        CheckResult(
            label=f"every {kind} {attribute} in {', '.join(allowed)}",
            passed=bool(ids) and all(records[rid].get(attribute) in allowed for rid in ids),
            detail=", ".join(seen) if seen else f"no {kind}",
        )
    ]


def _output(spec: OutputExpect, output: str) -> list[CheckResult]:
    lowered = output.lower()
    excerpt = _short(output, 160)
    results: list[CheckResult] = []
    if spec.contains_any:
        results.append(
            CheckResult(
                label=f"reply mentions one of {spec.contains_any}",
                passed=any(term.lower() in lowered for term in spec.contains_any),
                detail=excerpt,
            )
        )
    for term in spec.contains_all:
        results.append(
            CheckResult(
                label=f"reply mentions {term!r}", passed=term.lower() in lowered, detail=excerpt
            )
        )
    for term in spec.excludes:
        results.append(
            CheckResult(
                label=f"reply does not mention {term!r}",
                passed=term.lower() not in lowered,
                detail=excerpt,
            )
        )
    if spec.asks_question is not None:
        asks = "?" in output or "？" in output
        results.append(
            CheckResult(
                label="reply asks the user a question"
                if spec.asks_question
                else "reply asks no question",
                passed=asks == spec.asks_question,
                detail=excerpt,
            )
        )
    return results


# -- helpers -----------------------------------------------------------------------------


def _is_subsequence(wanted: Sequence[str], seen: Sequence[str]) -> bool:
    remaining = iter(seen)
    return all(any(name == item for item in remaining) for name in wanted)


def _plan_detail(plan: Plan | None) -> str:
    return "no plan" if plan is None else f"{plan.id} with {len(plan.stages)} stage(s)"


def _states(states: Iterable[Any]) -> str:
    return "/".join(str(state) for state in states)


def _describe(value: Any) -> str:
    if isinstance(value, dict) and len(value) == 1:
        (operator, operand) = next(iter(value.items()))
        return f"{operator} {operand!r}"
    return repr(value)


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, list | tuple):
        return " ".join(_flatten(v) for v in value)
    return "" if value is None else str(value)


def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["CheckResult", "Observation", "Range", "evaluate", "grounded_ids", "matches"]
