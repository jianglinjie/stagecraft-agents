"""Run cases through the real agents, with whatever models the caller supplies.

Each case gets a fresh runtime (Plan Store, workspace, session memory), so cases can run
concurrently and never see each other's state. Statuses mean different things:

* ``passed``: every structured check held, and the judge passed when the case asks for one;
* ``failed``: the agents did something a check refuses, including crashing a turn;
* ``error``: the measurement itself broke (the endpoint timed out or rate-limited, or the
  judge would not submit). Errors are retried once and never counted as agent failures.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal

from agents import Model
from pydantic import BaseModel, Field

from stagecraft.agents.runtime import build_runtime
from stagecraft.evals.cases import EvalCase
from stagecraft.evals.checks import CheckResult, Observation, evaluate, grounded_ids
from stagecraft.evals.judge import RUBRIC_VERSION, Judge, JudgeInput, JudgeResult
from stagecraft.evals.trace import (
    INFRA_ERRORS,
    CaseSession,
    EndpointRefused,
    Meter,
    MeteredModel,
    TurnRecord,
    is_fatal,
)
from stagecraft.plan import StageState
from stagecraft.tools.context import Role
from stagecraft.tools.fake import FakeWorkspace
from stagecraft.tools.results import StructuredToolError
from stagecraft.tools.retrieval import ReferenceIndex

CaseStatus = Literal["passed", "failed", "error"]
ModelsFor = Callable[[EvalCase], Mapping[Role, Model]]


class Usage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class TurnSummary(BaseModel):
    user: str
    auto: bool
    output: str
    error: str | None
    calls: dict[str, list[str]]
    dispatches: list[str] = Field(default_factory=list)
    seconds: float


class CaseResult(BaseModel):
    case_id: str
    category: str
    description: str
    attempt: int = 1
    status: CaseStatus
    checks: list[CheckResult] = Field(default_factory=list)
    judge: JudgeResult | None = None
    error: str | None = None
    retried: bool = False
    turns: list[TurnSummary] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    seconds: float = 0.0

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]


class SuiteMeta(BaseModel):
    label: str
    started_at: str
    model: str
    judge_model: str | None
    endpoint: str
    prompts: dict[str, str]
    rubric: str = RUBRIC_VERSION
    cases: int
    repeat: int
    concurrency: int
    seconds: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    references: str | None = None


class SuiteResult(BaseModel):
    meta: SuiteMeta
    results: list[CaseResult]


class SuiteAborted(RuntimeError):
    """The endpoint refused the account mid-run; no report should be written from what ran."""


async def run_case(
    case: EvalCase,
    *,
    models: Mapping[Role, Model],
    judge: Judge | None = None,
    instructions: Mapping[Role, str] | None = None,
    references: ReferenceIndex | None = None,
    attempt: int = 1,
) -> CaseResult:
    started = time.monotonic()
    meter = Meter()
    runtime = build_runtime(
        chat_id=f"eval-{case.id}",
        models={role: MeteredModel(model, role, meter) for role, model in models.items()},
        workspace=FakeWorkspace(render_delay_seconds=0),
        instructions=instructions,
        references=references,
    )
    session = CaseSession(runtime)
    checks: list[CheckResult] = []

    def finish(status: CaseStatus, *, error: str | None = None) -> CaseResult:
        total = meter.total()
        return CaseResult(
            case_id=case.id,
            category=case.category,
            description=case.description,
            attempt=attempt,
            status=status,
            checks=checks,
            error=error,
            turns=[_summary(turn) for turn in session.turns],
            usage=Usage(
                requests=total.requests,
                input_tokens=total.input_tokens,
                output_tokens=total.output_tokens,
            ),
            seconds=round(time.monotonic() - started, 2),
        )

    try:
        completed = await _play(case, session, checks)
    except INFRA_ERRORS as err:
        return finish("error", error=f"{type(err).__name__}: {err}")
    except Exception as err:
        if is_fatal(err):
            raise EndpointRefused(meter.refused or str(err)) from err
        raise
    if meter.refused is not None:
        # Refused inside a sub-agent: the dispatch tool reported it and the turn went on.
        raise EndpointRefused(meter.refused)
    if meter.infra_errors:
        # A sub-agent's endpoint failed and a dispatch tool reported it to the orchestrator:
        # whatever happened next says nothing about the agents.
        return finish("error", error=meter.infra_errors[0])

    if completed and case.final is not None:
        observed = Observation(session.turns)
        checks.extend(_labelled("final", evaluate(case.final, observed)))
    if case.grounded_ids:
        checks.append(grounded_ids(session.turns, session.plans()))

    result = finish("passed" if all(check.passed for check in checks) else "failed")
    if case.judge and judge is not None and result.status == "passed":
        try:
            result.judge = await judge.grade(judge_input(session))
        except (StructuredToolError, *INFRA_ERRORS) as err:
            result.status = "error"
            result.error = f"judge: {err}"
            return result
        except Exception as err:
            if is_fatal(err):
                raise EndpointRefused(f"judge: {err}") from err
            raise
        if not result.judge.passed:
            result.status = "failed"
    result.seconds = round(time.monotonic() - started, 2)
    return result


async def _play(case: EvalCase, session: CaseSession, checks: list[CheckResult]) -> bool:
    """Send the scripted turns, then the approval loop. False if a turn crashed."""
    for spec in case.turns:
        turn = await session.send(spec.user)
        label = f"turn {turn.index}"
        checks.extend(_labelled(label, evaluate(spec.expect, Observation([turn]))))
        if turn.error is not None:
            checks.append(CheckResult(label=f"{label} completed", passed=False, detail=turn.error))
            return False
    if case.approve is None:
        return True
    for _ in range(case.approve.max_turns):
        plan = session.plan()
        waiting = plan is not None and any(s.state == StageState.WAITING_USER for s in plan.stages)
        if plan is None or plan.open_questions or not waiting:
            return True
        turn = await session.send(case.approve.message, auto=True)
        if turn.error is not None:
            checks.append(
                CheckResult(label=f"turn {turn.index} completed", passed=False, detail=turn.error)
            )
            return False
        after = session.plan()
        if after is None or after.revision == plan.revision:
            return True
    return True


def judge_input(session: CaseSession) -> JudgeInput:
    last = session.turns[-1]
    plan = last.plan
    return JudgeInput(
        conversation=[
            {"user": turn.user, "assistant": turn.output or ""} for turn in session.turns
        ],
        plan=plan.summary() if plan is not None else None,
        open_questions=list(plan.open_questions) if plan is not None else [],
        artefacts=[_artefact(record_id, record) for record_id, record in last.workspace.items()],
        last_turn_tools=[call.label() for call in last.calls if call.role == "orchestrator"],
    )


async def run_suite(
    cases: Sequence[EvalCase],
    *,
    models_for: ModelsFor,
    meta: SuiteMeta,
    judge: Judge | None = None,
    instructions: Mapping[Role, str] | None = None,
    references: ReferenceIndex | None = None,
    on_result: Callable[[CaseResult, int, int], Awaitable[None] | None] | None = None,
) -> SuiteResult:
    """Every case ``meta.repeat`` times, ``meta.concurrency`` at a time.

    Raises :class:`SuiteAborted` when the endpoint refuses the account: cases not yet started
    are skipped, and nothing that ran is returned as if it had measured the agents.
    """
    started = time.monotonic()
    gate = asyncio.Semaphore(meta.concurrency)
    jobs = [(case, attempt) for attempt in range(1, meta.repeat + 1) for case in cases]
    results: list[CaseResult | None] = [None] * len(jobs)
    finished = 0
    refused: str | None = None

    async def run(index: int, case: EvalCase, attempt: int) -> None:
        nonlocal finished, refused
        async with gate:
            if refused is not None:
                return
            try:
                result = await run_case(
                    case,
                    models=models_for(case),
                    judge=judge,
                    instructions=instructions,
                    references=references,
                    attempt=attempt,
                )
                if result.status == "error":
                    retry = await run_case(
                        case,
                        models=models_for(case),
                        judge=judge,
                        instructions=instructions,
                        references=references,
                        attempt=attempt,
                    )
                    retry.retried = True
                    result = retry
            except EndpointRefused as err:
                refused = refused or str(err)
                return
        results[index] = result
        finished += 1
        if on_result is not None:
            outcome = on_result(result, finished, len(jobs))
            if outcome is not None:
                await outcome

    await asyncio.gather(*(run(i, case, attempt) for i, (case, attempt) in enumerate(jobs)))
    if refused is not None:
        raise SuiteAborted(f"the endpoint refused the account ({refused}); fix it and rerun")
    done = [result for result in results if result is not None]
    meta = meta.model_copy(
        update={
            "seconds": round(time.monotonic() - started, 1),
            "usage": sum((result.usage for result in done), Usage()),
        }
    )
    return SuiteResult(meta=meta, results=done)


def prompt_fingerprints(
    instructions: Mapping[Role, str], sources: Mapping[str, str] | None = None
) -> dict[str, str]:
    """``role -> sha256[:12] (source)`` for every role's base prompt under test."""
    from stagecraft.agents.executor import EXECUTOR_INSTRUCTIONS
    from stagecraft.agents.orchestrator import ORCHESTRATOR_INSTRUCTIONS
    from stagecraft.agents.planner import PLANNER_INSTRUCTIONS
    from stagecraft.agents.router import ROUTER_INSTRUCTIONS

    defaults: dict[Role, str] = {
        "orchestrator": ORCHESTRATOR_INSTRUCTIONS,
        "router": ROUTER_INSTRUCTIONS,
        "planner": PLANNER_INSTRUCTIONS,
        "executor": EXECUTOR_INSTRUCTIONS,
    }
    fingerprints: dict[str, str] = {}
    for role, default in defaults.items():
        text = instructions.get(role, default)
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        source = (sources or {}).get(role, "built-in")
        fingerprints[role] = f"{digest} ({source})"
    return fingerprints


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _labelled(prefix: str, checks: list[CheckResult]) -> list[CheckResult]:
    return [check.model_copy(update={"label": f"{prefix}: {check.label}"}) for check in checks]


def _summary(turn: TurnRecord) -> TurnSummary:
    return TurnSummary(
        user=turn.user,
        auto=turn.auto,
        output=turn.output or "",
        error=turn.error,
        calls=turn.calls_by_role(),
        dispatches=[call.payload() for call in turn.calls if call.name.startswith("dispatch_")],
        seconds=round(turn.seconds, 2),
    )


def _artefact(record_id: str, record: Mapping[str, object]) -> str:
    detail = record.get("format") or record.get("tone")
    return f"{record_id} ({detail})" if detail else record_id
