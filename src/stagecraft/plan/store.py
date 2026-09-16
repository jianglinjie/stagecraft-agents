"""PlanStore: SQLite persistence with a version check on every write.

The whole plan is one JSON document in one row, and every write is a
compare-and-swap on its revision::

    UPDATE plans SET document = ?, revision = revision + 1
    WHERE id = ? AND revision = ?

``rowcount`` says who won. A writer holding a stale revision changes nothing and
gets ``revision_conflict``, so two agents that both read revision 3 cannot both
turn it into revision 4.

Two guards, for two kinds of write:

* **authoring** (contracts, runtime refs) is protected by ``expected_revision``
  when the caller supplies one;
* **progression** (state changes) is additionally protected by the state machine
  itself: ``done`` is terminal, so a replayed "mark done" cannot double-apply.

Every write names its ``role``, and the store refuses writes outside that role's
half of the stage.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from stagecraft.plan.errors import PlanNotFound, RevisionConflict, RoleDenied
from stagecraft.plan.model import (
    Plan,
    ReviewKind,
    RuntimeRef,
    Stage,
    StageContract,
    StageState,
)
from stagecraft.plan.state_machine import check_transition

Role = str

#: Which role may perform which write. Anything absent is refused.
WRITE_PERMISSIONS: dict[str, frozenset[Role]] = {
    "create_plan": frozenset({"orchestrator"}),
    "write_contract": frozenset({"planner"}),
    "attach_runtime": frozenset({"executor"}),
    "update_state": frozenset({"orchestrator"}),
    "ask_user": frozenset({"orchestrator"}),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    document    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plans_chat ON plans (chat_id, created_at);
"""


class PlanStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA)

    # -- reads -----------------------------------------------------------------

    def get_plan(self, plan_id: str) -> Plan:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise PlanNotFound(f"no plan with id {plan_id!r}")
        return Plan.model_validate_json(row[0])

    def latest_for_chat(self, chat_id: str) -> Plan | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM plans WHERE chat_id = ? ORDER BY created_at DESC, id DESC",
                (chat_id,),
            ).fetchone()
        return None if row is None else Plan.model_validate_json(row[0])

    def get_stage(self, plan_id: str, stage_id: str) -> tuple[Plan, Stage]:
        plan = self.get_plan(plan_id)
        stage = plan.stage(stage_id)
        if stage is None:
            known = ", ".join(s.id for s in plan.ordered()) or "none"
            raise PlanNotFound(
                f"plan {plan_id} has no stage {stage_id!r}",
                hint=f"Known stages: {known}.",
            )
        return plan, stage

    # -- writes ----------------------------------------------------------------

    def create_plan(self, *, chat_id: str, objective: str, role: Role) -> Plan:
        _require_role(role, "create_plan")
        now = _now()
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
            plan = Plan(id=f"plan_{count + 1:04d}", chat_id=chat_id, objective=objective)
            self._conn.execute(
                "INSERT INTO plans (id, chat_id, revision, document, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (plan.id, chat_id, plan.revision, plan.model_dump_json(), now, now),
            )
        return plan

    def write_stage_contract(
        self,
        *,
        plan_id: str,
        role: Role,
        contract: StageContract,
        stage_id: str | None = None,
        order: int | None = None,
        expected_revision: int | None = None,
    ) -> tuple[Plan, Stage]:
        """Author a new stage (no ``stage_id``) or rewrite a pending stage's contract."""
        _require_role(role, "write_contract")

        def mutate(plan: Plan) -> Stage:
            if stage_id is None:
                new_id = f"stage_{len(plan.stages) + 1:02d}"
                stage = Stage(
                    id=new_id,
                    order=order if order is not None else len(plan.stages) + 1,
                    goal=contract.goal,
                    contract=contract,
                )
                plan.stages.append(stage)
                return stage
            stage = _stage_or_raise(plan, stage_id)
            if stage.state not in (StageState.PENDING, StageState.WAITING_USER):
                raise RoleDenied(
                    f"stage {stage_id} is {stage.state}; its contract is frozen",
                    hint="Only pending or under-review stages can be re-authored.",
                )
            stage.contract = contract
            stage.goal = contract.goal
            stage.questions = []
            if order is not None:
                stage.order = order
            return stage

        return self._mutate(plan_id, expected_revision, mutate)

    def attach_runtime(
        self,
        *,
        plan_id: str,
        stage_id: str,
        role: Role,
        refs: list[RuntimeRef],
        notes: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[Plan, Stage]:
        """Append produced refs to a stage that is being executed."""
        _require_role(role, "attach_runtime")

        def mutate(plan: Plan) -> Stage:
            stage = _stage_or_raise(plan, stage_id)
            if stage.state != StageState.DOING:
                raise RoleDenied(
                    f"stage {stage_id} is {stage.state}; runtime can only be attached while doing",
                    hint="Ask the orchestrator to move the stage to doing first.",
                )
            known = {ref.ref_id for ref in stage.runtime.refs}
            stage.runtime.refs.extend(ref for ref in refs if ref.ref_id not in known)
            if notes:
                stage.runtime.notes = notes
            stage.runtime.attempts += 1
            return stage

        return self._mutate(plan_id, expected_revision, mutate)

    def update_stage_state(
        self,
        *,
        plan_id: str,
        stage_id: str,
        role: Role,
        target: StageState,
        user_confirmed: bool = False,
        review_kind: ReviewKind | None = None,
        blocked_reason: str | None = None,
        questions: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[Plan, Stage]:
        _require_role(role, "update_state")

        def mutate(plan: Plan) -> Stage:
            stage = _stage_or_raise(plan, stage_id)
            check_transition(stage, target, user_confirmed=user_confirmed, review_kind=review_kind)
            stage.state = target
            stage.review_kind = review_kind if target == StageState.WAITING_USER else None
            stage.blocked_reason = blocked_reason if target == StageState.BLOCKED else None
            stage.questions = list(questions or []) if target == StageState.WAITING_USER else []
            return stage

        return self._mutate(plan_id, expected_revision, mutate)

    def ask_user(
        self, *, plan_id: str, role: Role, questions: list[str]
    ) -> tuple[Plan, Stage | None]:
        """Record that planning is blocked on the user.

        The questions go on the plan, and on the first authored pending stage when there
        is one, which also moves into plan review: the stage cannot be started while its
        author is still waiting for answers.
        """
        _require_role(role, "ask_user")
        moved: list[Stage] = []

        def mutate(plan: Plan) -> Stage:
            plan.open_questions = list(questions)
            stage = next(
                (s for s in plan.ordered() if s.state == StageState.PENDING and s.contract),
                None,
            )
            if stage is not None:
                check_transition(stage, StageState.WAITING_USER, review_kind=ReviewKind.PLAN_REVIEW)
                stage.state = StageState.WAITING_USER
                stage.review_kind = ReviewKind.PLAN_REVIEW
                stage.questions = list(questions)
                moved.append(stage)
            return stage  # type: ignore[return-value]

        plan, _ = self._mutate(plan_id, None, mutate)
        return plan, (moved[0] if moved else None)

    def clear_questions(self, *, plan_id: str, role: Role) -> Plan:
        _require_role(role, "ask_user")

        def mutate(plan: Plan) -> Stage:
            plan.open_questions = []
            return None  # type: ignore[return-value]

        plan, _ = self._mutate(plan_id, None, mutate)
        return plan

    # -- internals -------------------------------------------------------------

    def _mutate(
        self,
        plan_id: str,
        expected_revision: int | None,
        mutate: Callable[[Plan], Stage],
    ) -> tuple[Plan, Stage]:
        with self._lock:
            plan = self.get_plan(plan_id)
            if expected_revision is not None and expected_revision != plan.revision:
                raise RevisionConflict(
                    f"plan {plan_id} is at revision {plan.revision}, not {expected_revision}",
                    hint="Re-read the plan and decide again against the current revision.",
                )
            read_revision = plan.revision
            stage = mutate(plan)
            plan.revision = read_revision + 1
            cursor = self._conn.execute(
                "UPDATE plans SET document = ?, revision = ?, updated_at = ? "
                "WHERE id = ? AND revision = ?",
                (plan.model_dump_json(), plan.revision, _now(), plan_id, read_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    f"plan {plan_id} changed while this write was being applied",
                    hint="Re-read the plan and retry.",
                )
        return plan, stage


def _require_role(role: Role, action: str) -> None:
    allowed = WRITE_PERMISSIONS[action]
    if role not in allowed:
        raise RoleDenied(
            f"role {role!r} may not {action.replace('_', ' ')}",
            hint=f"Only {', '.join(sorted(allowed))} may do that.",
        )


def _stage_or_raise(plan: Plan, stage_id: str) -> Stage:
    stage = plan.stage(stage_id)
    if stage is None:
        known = ", ".join(s.id for s in plan.ordered()) or "none"
        raise PlanNotFound(f"plan {plan.id} has no stage {stage_id!r}", hint=f"Known: {known}.")
    return stage


def _now() -> str:
    return datetime.now(UTC).isoformat()
