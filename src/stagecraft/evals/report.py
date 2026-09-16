"""Markdown reports: pass rates by category, then every failure with what was observed."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from stagecraft.evals.cases import CATEGORIES
from stagecraft.evals.judge import CRITERIA
from stagecraft.evals.runner import CaseResult, SuiteResult

REPLY_EXCERPT = 400


@dataclass
class Tally:
    runs: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0

    def add(self, result: CaseResult) -> None:
        self.runs += 1
        if result.status == "passed":
            self.passed += 1
        elif result.status == "failed":
            self.failed += 1
        else:
            self.errors += 1

    @property
    def rate(self) -> float:
        """Pass rate over runs that measured something: errors are excluded, and shown."""
        measured = self.runs - self.errors
        return self.passed / measured if measured else 0.0


def tally(results: Iterable[CaseResult]) -> dict[str, Tally]:
    tallies: dict[str, Tally] = {category: Tally() for category in CATEGORIES}
    overall = Tally()
    for result in results:
        tallies.setdefault(result.category, Tally()).add(result)
        overall.add(result)
    tallies["all"] = overall
    return tallies


def render_report(suite: SuiteResult) -> str:
    meta = suite.meta
    tallies = tally(suite.results)
    lines = [
        f"# Eval report: {meta.label}",
        "",
        f"- Run: {meta.started_at}, {meta.seconds:.0f}s wall, concurrency {meta.concurrency}",
        f"- Model under test: `{meta.model}` at `{meta.endpoint}`",
        f"- Judge: {f'`{meta.judge_model}`, {meta.rubric}' if meta.judge_model else 'off'}",
        f"- Cases: {meta.cases}, repeated {meta.repeat}x",
        f"- Usage: {meta.usage.requests} model requests, {meta.usage.input_tokens:,} input "
        f"tokens, {meta.usage.output_tokens:,} output tokens",
        "- Prompts: "
        + ", ".join(f"{role} `{fingerprint}`" for role, fingerprint in meta.prompts.items()),
        "",
        "## Pass rate by category",
        "",
        "| Category | Runs | Passed | Failed | Errors | Pass rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, entry in tallies.items():
        label = "**all**" if name == "all" else name
        lines.append(
            f"| {label} | {entry.runs} | {entry.passed} | {entry.failed} | {entry.errors} "
            f"| {entry.rate:.0%} |"
        )
    lines += ["", "Pass rate excludes errors (the endpoint failed, not the agents).", ""]

    judged = [r for r in suite.results if r.judge is not None]
    if judged:
        lines += [
            "## Judge scores",
            "",
            f"{len(judged)} judged run(s), {sum(1 for r in judged if r.judge and r.judge.passed)}"
            " passed the rubric (every criterion >= 3, mean >= 4).",
            "",
            "| Criterion | Mean |",
            "|---|---:|",
        ]
        for criterion in CRITERIA:
            scores = [r.judge.scores[criterion] for r in judged if r.judge]
            lines.append(f"| {criterion} | {sum(scores) / len(scores):.2f} |")
        lines.append("")

    flaky = _flaky(suite.results) if meta.repeat > 1 else []
    if flaky:
        lines += ["## Flaky cases", "", *(f"- `{case_id}`: {text}" for case_id, text in flaky), ""]

    failures = [r for r in suite.results if r.status != "passed"]
    lines += ["## Failures", ""]
    if not failures:
        lines.append("None.")
    for result in sorted(failures, key=lambda r: (r.category, r.case_id, r.attempt)):
        lines += _failure(result, repeat=meta.repeat)
    return "\n".join(lines).rstrip() + "\n"


def render_comparison(before: SuiteResult, after: SuiteResult) -> str:
    """Per-category deltas and the cases whose outcome changed."""
    old, new = tally(before.results), tally(after.results)
    lines = [
        f"| Category | {before.meta.label} | {after.meta.label} | Change |",
        "|---|---:|---:|---:|",
    ]
    for name in new:
        a, b = old.get(name, Tally()), new[name]
        label = "**all**" if name == "all" else name
        delta = (b.rate - a.rate) * 100
        lines.append(
            f"| {label} | {a.passed}/{a.runs - a.errors} ({a.rate:.0%}) "
            f"| {b.passed}/{b.runs - b.errors} ({b.rate:.0%}) | {delta:+.0f} pt |"
        )
    fixed, broke = _flips(before.results, after.results)
    lines += ["", f"Now passing ({len(fixed)}): " + (", ".join(f"`{c}`" for c in fixed) or "none")]
    lines += [f"Now failing ({len(broke)}): " + (", ".join(f"`{c}`" for c in broke) or "none")]
    return "\n".join(lines) + "\n"


def _failure(result: CaseResult, *, repeat: int) -> list[str]:
    title = f"### `{result.case_id}` ({result.category}, {result.status})"
    if repeat > 1:
        title += f", run {result.attempt}"
    lines = [title, "", result.description, ""]
    if result.error:
        lines += [f"- Error: {result.error}" + (" (after one retry)" if result.retried else "")]
    for check in result.failed_checks:
        lines.append(f"- Failed: {check.label}. Saw: {check.detail}")
    if result.judge is not None and not result.judge.passed:
        scores = ", ".join(f"{name} {score}" for name, score in result.judge.scores.items())
        lines.append(f"- Judge: {scores}. {result.judge.rationale}")
    lines.append("")
    for index, turn in enumerate(result.turns, start=1):
        who = "approval loop" if turn.auto else "user"
        lines.append(f"Turn {index} ({who}): {_quote(turn.user, 200)}")
        for role, calls in turn.calls.items():
            lines.append(f"- {role}: {' → '.join(_collapse(calls))}")
        for dispatch in turn.dispatches:
            lines.append(f"- sent {_quote(dispatch, 240)}")
        if turn.error:
            lines.append(f"- error: {turn.error}")
        lines.append(f"- reply: {_quote(turn.output, REPLY_EXCERPT)}")
        lines.append("")
    return lines


def _flaky(results: Sequence[CaseResult]) -> list[tuple[str, str]]:
    outcomes: dict[str, list[str]] = {}
    for result in results:
        outcomes.setdefault(result.case_id, []).append(result.status)
    return [
        (case_id, f"{statuses.count('passed')}/{len(statuses)} runs passed")
        for case_id, statuses in sorted(outcomes.items())
        if 0 < statuses.count("passed") < len(statuses)
    ]


def _flips(
    before: Sequence[CaseResult], after: Sequence[CaseResult]
) -> tuple[list[str], list[str]]:
    def passing(results: Sequence[CaseResult]) -> dict[str, bool | None]:
        seen: dict[str, list[str]] = {}
        for result in results:
            seen.setdefault(result.case_id, []).append(result.status)
        # A case passes when every measured run passed; all-error cases say nothing.
        return {
            case_id: (all(s == "passed" for s in measured) if measured else None)
            for case_id, statuses in seen.items()
            for measured in [[s for s in statuses if s != "error"]]
        }

    old, new = passing(before), passing(after)
    fixed = sorted(c for c, ok in new.items() if ok is True and old.get(c) is False)
    broke = sorted(c for c, ok in new.items() if ok is False and old.get(c) is True)
    return fixed, broke


def _collapse(labels: Sequence[str]) -> list[str]:
    collapsed: list[str] = []
    for label in labels:
        if collapsed and collapsed[-1].split(" ×")[0] == label:
            count = int(collapsed[-1].split(" ×")[1]) + 1 if " ×" in collapsed[-1] else 2
            collapsed[-1] = f"{label} ×{count}"
        else:
            collapsed.append(label)
    return collapsed


def _quote(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return f"“{flat}”" if flat else "(empty)"
