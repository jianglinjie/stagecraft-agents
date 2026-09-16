"""The stage state machine.

Progress is a transition, not a sentence in the transcript. Everything that moves
work forward comes through :func:`check_transition`, which is why two replicas, a
retry and a confused model cannot disagree about where a stage is.

The table::

    pending ──> waiting_user(plan_review) ──[user_confirmed]──> doing ──> done
                     │                                           │  ▲
                     └──> omitted                                │  │
                                                  blocked <──────┘  │
                                                     └──────────────┘
    doing ──> waiting_user(result_review) ──[user_confirmed]──> done | doing

There is deliberately **no edge from pending to doing**. A stage cannot start
without first being presented for review, and it cannot leave review without an
explicit confirmation. The gate is not a rule a model has to remember; it is a
missing edge.

The other rules, each guarding a different mistake:

* ``contract_required`` — a stage cannot be reviewed or started before the
  Planner has written what it is for. Otherwise the Executor invents the job.
* ``results_required`` — a stage cannot be ``done``, or sent for result review,
  with nothing produced. A model that "finishes" by saying so is refused.
* terminal states — ``done`` and ``omitted`` accept nothing, so a replayed or
  retried "mark done" cannot double-apply.
"""

from __future__ import annotations

from stagecraft.plan.errors import (
    ConfirmationRequired,
    ContractRequired,
    IllegalTransition,
    ResultsRequired,
)
from stagecraft.plan.model import ReviewKind, Stage, StageState

S = StageState

TRANSITIONS: dict[StageState, frozenset[StageState]] = {
    S.PENDING: frozenset({S.WAITING_USER, S.OMITTED}),
    S.WAITING_USER: frozenset({S.DOING, S.DONE, S.OMITTED}),
    S.DOING: frozenset({S.DONE, S.BLOCKED, S.WAITING_USER}),
    S.BLOCKED: frozenset({S.DOING, S.OMITTED}),
    S.DONE: frozenset(),
    S.OMITTED: frozenset(),
}

TERMINAL = frozenset({S.DONE, S.OMITTED})


def check_transition(
    stage: Stage,
    target: StageState,
    *,
    user_confirmed: bool = False,
    review_kind: ReviewKind | None = None,
) -> None:
    """Raise a :class:`~stagecraft.plan.errors.PlanError` unless the move is allowed."""
    current = stage.state
    allowed = TRANSITIONS[current]
    if target not in allowed:
        allowed_text = ", ".join(sorted(allowed)) or "nothing (terminal state)"
        raise IllegalTransition(
            f"stage {stage.id} cannot go {current} -> {target}",
            hint=f"From {current} the legal targets are: {allowed_text}.",
        )

    if target == S.WAITING_USER:
        expected = ReviewKind.PLAN_REVIEW if current == S.PENDING else ReviewKind.RESULT_REVIEW
        if review_kind != expected:
            raise IllegalTransition(
                f"stage {stage.id} entering review from {current} must use {expected}",
                hint=f"Request waiting_user with review_kind={expected}.",
            )

    if target in (S.WAITING_USER, S.DOING) and stage.contract is None:
        raise ContractRequired(
            f"stage {stage.id} has no contract yet",
            hint="Dispatch the Planner to author this stage first.",
        )

    if current == S.WAITING_USER and target in (S.DOING, S.DONE):
        if target == S.DONE and stage.review_kind != ReviewKind.RESULT_REVIEW:
            raise IllegalTransition(
                f"stage {stage.id} is in plan review; it has not been executed",
                hint="Confirm the plan into doing, execute it, then request done.",
            )
        if not user_confirmed:
            raise ConfirmationRequired(
                f"stage {stage.id} is waiting on {stage.review_kind}",
                hint=(
                    "Present the review to the user, end the turn, and call again with "
                    "user_confirmed=true only after they approve."
                ),
            )

    produces_results = target == S.DONE or (
        target == S.WAITING_USER and review_kind == ReviewKind.RESULT_REVIEW
    )
    if produces_results and not stage.runtime.refs:
        raise ResultsRequired(
            f"stage {stage.id} has produced nothing",
            hint="Dispatch the Executor and let it attach refs before this transition.",
        )


def next_action(stage: Stage) -> str:
    """The authoritative hint for the Orchestrator. Never infer state from prose."""
    match stage.state:
        case S.PENDING if stage.contract is None:
            return "dispatch_planner to author this stage"
        case S.PENDING:
            return "request waiting_user with review_kind=plan_review and present the stage"
        case S.WAITING_USER:
            return (
                f"present the {stage.review_kind} to the user and end the turn; after explicit "
                "approval request the next state with user_confirmed=true"
            )
        case S.DOING:
            return "dispatch_executor for this stage, then request done"
        case S.BLOCKED:
            return f"resolve the block ({stage.blocked_reason or 'unknown'}), then request doing"
        case S.DONE:
            return "move on to the next pending stage"
        case _:
            return "stage is omitted; skip it"
