"""``python evals/run.py``: run the eval set, or compare two runs.

    uv run --env-file .env python evals/run.py                        # everything
    uv run --env-file .env python evals/run.py --category routing     # one category
    uv run --env-file .env python evals/run.py --case robust-vague-request-asks
    uv run --env-file .env python evals/run.py --label baseline --out docs/evals \\
        --prompt orchestrator=evals/prompts/orchestrator-v1.md --judge-model deepseek-v4-pro
    uv run python evals/run.py compare docs/evals/a.json docs/evals/b.json

A run writes ``<out>/<label>.md`` (the report) and ``<out>/<label>.json`` (the results
``compare`` reads). The judge uses the same endpoint; ``--judge-model`` picks a different
model on it, which is better than letting a model grade its own replies.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from agents import set_tracing_disabled

from stagecraft.agents.runtime import same_model_for_all_roles
from stagecraft.agents.single import build_openai_model
from stagecraft.config import MissingConfigError, ModelConfig
from stagecraft.evals.cases import CATEGORIES, CaseLoadError, EvalCase, load_cases
from stagecraft.evals.judge import Judge, ModelJudge
from stagecraft.evals.report import render_comparison, render_report, tally
from stagecraft.evals.runner import (
    CaseResult,
    ModelsFor,
    SuiteAborted,
    SuiteMeta,
    SuiteResult,
    now_iso,
    prompt_fingerprints,
    run_suite,
)
from stagecraft.tools.context import Role
from stagecraft.tools.retrieval import ReferenceIndex, reference_index_from_env

ROLES: tuple[Role, ...] = ("orchestrator", "router", "planner", "executor")


def main(
    argv: Sequence[str] | None = None,
    *,
    models_for: ModelsFor | None = None,
    judge: Judge | None = None,
    model_name: str | None = None,
) -> int:
    """``models_for`` and ``judge`` replace the endpoint, so tests can run the CLI offline."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("run", "compare", "-h", "--help"):
        args.insert(0, "run")
    parsed = _parser().parse_args(args)
    if parsed.command == "compare":
        return _compare(parsed.before, parsed.after)
    return asyncio.run(_run(parsed, models_for=models_for, judge=judge, model_name=model_name))


async def _run(
    args: argparse.Namespace,
    *,
    models_for: ModelsFor | None,
    judge: Judge | None,
    model_name: str | None,
) -> int:
    set_tracing_disabled(True)
    try:
        cases = _select(load_cases(args.cases), args.category, args.case)
        instructions, sources = _read_prompts(args.prompt)
    except (CaseLoadError, ValueError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    if not cases:
        print("error: no case matches the filters", file=sys.stderr)
        return 2

    endpoint = "offline"
    judge_name = judge.model_name if judge is not None else None
    references: ReferenceIndex | None = None
    if models_for is None:
        try:
            config = ModelConfig.from_env()
        except MissingConfigError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        model = build_openai_model(config, max_retries=args.max_retries)
        models_for = _same_model(model)
        references = reference_index_from_env()
        await references.load()
        model_name = config.model
        endpoint = urlparse(config.base_url).netloc or config.base_url
        if not args.no_judge and judge is None:
            judge_name = args.judge_model or os.environ.get("EVAL_JUDGE_MODEL") or config.model
            judge_config = config.model_copy(update={"model": judge_name})
            judge = ModelJudge(
                build_openai_model(judge_config, max_retries=args.max_retries),
                model_name=judge_name,
            )
    if args.no_judge:
        judge, judge_name = None, None

    label = args.label or datetime.now(UTC).strftime("run-%Y%m%d-%H%M")
    out = Path(args.out)
    report_path, results_path = out / f"{label}.md", out / f"{label}.json"
    existing = [str(path) for path in (report_path, results_path) if path.exists()]
    if existing and not args.overwrite:
        # A rerun under the same label once replaced a good baseline with a broken run.
        print(
            f"error: {', '.join(existing)} already exists; choose another --label or pass "
            "--overwrite",
            file=sys.stderr,
        )
        return 2
    meta = SuiteMeta(
        label=label,
        started_at=now_iso(),
        model=model_name or "fake",
        judge_model=judge_name,
        endpoint=endpoint,
        prompts=prompt_fingerprints(instructions, sources),
        cases=len(cases),
        repeat=args.repeat,
        concurrency=args.concurrency,
        references=_describe_references(references),
    )
    print(
        f"running {len(cases)} case(s) x{args.repeat} on {meta.model} ({endpoint})",
        file=sys.stderr,
    )
    try:
        suite = await run_suite(
            cases,
            models_for=models_for,
            meta=meta,
            judge=judge,
            instructions=instructions,
            references=references,
            on_result=_progress,
        )
    except SuiteAborted as err:
        print(f"aborted, no report written: {err}", file=sys.stderr)
        return 3

    out.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(suite), encoding="utf-8")
    results_path.write_text(suite.model_dump_json(indent=1), encoding="utf-8")

    for name, entry in tally(suite.results).items():
        print(
            f"{name:<11} {entry.passed:>3}/{entry.runs - entry.errors:<3} {entry.rate:>5.0%}"
            f"  errors {entry.errors}"
        )
    print(f"report:  {report_path}\nresults: {results_path}")
    return 0


def _compare(before: str, after: str) -> int:
    try:
        old = SuiteResult.model_validate_json(Path(before).read_text(encoding="utf-8"))
        new = SuiteResult.model_validate_json(Path(after).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(render_comparison(old, new), end="")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals/run.py", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run cases and write a report (the default)")
    run.add_argument("--cases", default="evals/cases", help="directory of *.yaml case files")
    run.add_argument("--category", action="append", choices=CATEGORIES, default=[])
    run.add_argument("--case", action="append", default=[], help="run only this case id")
    run.add_argument("--label", help="report name; defaults to a timestamp")
    run.add_argument("--out", default=".data/evals", help="where to write the report")
    run.add_argument("--overwrite", action="store_true", help="replace a report with this label")
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--repeat", type=int, default=1, help="runs per case, to expose flakiness")
    run.add_argument(
        "--prompt",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="replace a role's base prompt with a file's contents",
    )
    run.add_argument("--judge-model", help="model for the judge (default: EVAL_JUDGE_MODEL)")
    run.add_argument("--no-judge", action="store_true", help="structured checks only")
    run.add_argument("--max-retries", type=int, default=4, help="HTTP retries per model request")

    compare = commands.add_parser("compare", help="compare two results files")
    compare.add_argument("before")
    compare.add_argument("after")
    return parser


def _select(
    cases: Sequence[EvalCase], categories: Sequence[str], ids: Sequence[str]
) -> list[EvalCase]:
    unknown = set(ids) - {case.id for case in cases}
    if unknown:
        raise ValueError(f"unknown case id(s): {', '.join(sorted(unknown))}")
    return [
        case
        for case in cases
        if (not categories or case.category in categories) and (not ids or case.id in ids)
    ]


def _read_prompts(entries: Sequence[str]) -> tuple[dict[Role, str], dict[str, str]]:
    instructions: dict[Role, str] = {}
    sources: dict[str, str] = {}
    for entry in entries:
        role, _, path = entry.partition("=")
        if role not in ROLES or not path:
            raise ValueError(f"--prompt expects ROLE=PATH with ROLE in {ROLES}, got {entry!r}")
        instructions[role] = Path(path).read_text(encoding="utf-8").strip()  # type: ignore[index]
        sources[role] = path
    return instructions, sources


def _same_model(model: object) -> ModelsFor:
    def models_for(_case: EvalCase) -> Mapping[Role, object]:
        return same_model_for_all_roles(model)  # type: ignore[arg-type]

    return models_for  # type: ignore[return-value]


def _describe_references(index: ReferenceIndex | None) -> str:
    if index is None:
        return "none"
    stats = index.stats()
    return f"{stats.chunks} sections, {stats.mode}"


def _progress(result: CaseResult, finished: int, total: int) -> None:
    width = len(str(total))
    line = (
        f"[{finished:>{width}}/{total}] {result.status:<6} {result.case_id}"
        f" ({result.usage.requests} req, {result.seconds:.0f}s)"
    )
    for check in result.failed_checks[:3]:
        line += f"\n    {check.label}: {check.detail[:140]}"
    if result.error:
        line += f"\n    {result.error[:160]}"
    print(line, file=sys.stderr)
