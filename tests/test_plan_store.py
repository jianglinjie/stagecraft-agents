import threading
from pathlib import Path

import pytest

from stagecraft.plan import (
    WRITE_PERMISSIONS,
    PlanNotFound,
    PlanStore,
    ReviewKind,
    RevisionConflict,
    RoleDenied,
    RuntimeRef,
    StageContract,
    StageState,
    WorkItem,
)

S = StageState


def contract(goal: str = "outline", inputs: list[str] | None = None) -> StageContract:
    return StageContract(
        goal=goal,
        inputs=inputs or [],
        work_items=[WorkItem(id="w1", name="outline", instruction="write it")],
        acceptance="has sections",
    )


def ref(ref_id: str = "outline_0001") -> RuntimeRef:
    return RuntimeRef(ref_id=ref_id, kind="outline", summary="3 sections", work_item_id="w1")


def test_every_write_bumps_the_revision() -> None:
    store = PlanStore()
    plan = store.create_plan(chat_id="c1", objective="series", role="orchestrator")
    assert (plan.id, plan.revision) == ("plan_0001", 0)

    plan, stage = store.write_stage_contract(plan_id=plan.id, role="planner", contract=contract())
    assert (plan.revision, stage.id, stage.state) == (1, "stage_01", S.PENDING)

    plan, _ = store.update_stage_state(
        plan_id=plan.id,
        stage_id="stage_01",
        role="orchestrator",
        target=S.WAITING_USER,
        review_kind=ReviewKind.PLAN_REVIEW,
    )
    assert plan.revision == 2
    assert store.get_plan(plan.id).revision == 2


def test_stale_expected_revision_is_refused_and_changes_nothing() -> None:
    store = PlanStore()
    plan = store.create_plan(chat_id="c1", objective="o", role="orchestrator")
    store.write_stage_contract(plan_id=plan.id, role="planner", contract=contract())

    with pytest.raises(RevisionConflict) as err:
        store.write_stage_contract(
            plan_id=plan.id, role="planner", contract=contract("second"), expected_revision=0
        )
    assert err.value.code == "revision_conflict"
    assert "revision 1, not 0" in str(err.value)

    after = store.get_plan(plan.id)
    assert after.revision == 1
    assert [s.goal for s in after.stages] == ["outline"]

    store.write_stage_contract(
        plan_id=plan.id, role="planner", contract=contract("second"), expected_revision=1
    )
    assert store.get_plan(plan.id).revision == 2


def test_two_writers_holding_the_same_revision_cannot_both_win() -> None:
    store = PlanStore()
    plan = store.create_plan(chat_id="c1", objective="o", role="orchestrator")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def author(goal: str) -> None:
        barrier.wait()
        try:
            store.write_stage_contract(
                plan_id=plan.id, role="planner", contract=contract(goal), expected_revision=0
            )
            outcomes.append("won")
        except RevisionConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=author, args=(g,)) for g in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["conflict", "won"]
    assert len(store.get_plan(plan.id).stages) == 1


def test_sql_compare_and_swap_catches_a_write_from_another_connection(tmp_path: Path) -> None:
    path = tmp_path / "plans.db"
    first, second = PlanStore(path), PlanStore(path)
    plan = first.create_plan(chat_id="c1", objective="o", role="orchestrator")

    original_get = first.get_plan
    sneaked = False

    def get_then_sneak_a_write(plan_id: str):  # type: ignore[no-untyped-def]
        nonlocal sneaked
        read = original_get(plan_id)
        if not sneaked:
            sneaked = True
            second.write_stage_contract(plan_id=plan_id, role="planner", contract=contract("x"))
        return read

    first.get_plan = get_then_sneak_a_write  # type: ignore[method-assign]
    with pytest.raises(RevisionConflict, match="changed while"):
        first.write_stage_contract(plan_id=plan.id, role="planner", contract=contract("y"))

    final = second.get_plan(plan.id)
    assert final.revision == 1
    assert [s.goal for s in final.stages] == ["x"]


@pytest.mark.parametrize(
    ("role", "action"),
    [
        ("planner", "create_plan"),
        ("executor", "create_plan"),
        ("orchestrator", "write_contract"),
        ("executor", "write_contract"),
        ("orchestrator", "attach_runtime"),
        ("planner", "attach_runtime"),
        ("planner", "update_state"),
        ("executor", "update_state"),
        ("router", "update_state"),
    ],
)
def test_role_guard_refuses_writes_outside_the_role(role: str, action: str) -> None:
    assert role not in WRITE_PERMISSIONS[action]
    store = PlanStore()
    plan = store.create_plan(chat_id="c1", objective="o", role="orchestrator")
    store.write_stage_contract(plan_id=plan.id, role="planner", contract=contract())
    calls = {
        "create_plan": lambda: store.create_plan(chat_id="c1", objective="o", role=role),
        "write_contract": lambda: store.write_stage_contract(
            plan_id=plan.id, role=role, contract=contract()
        ),
        "attach_runtime": lambda: store.attach_runtime(
            plan_id=plan.id, stage_id="stage_01", role=role, refs=[ref()]
        ),
        "update_state": lambda: store.update_stage_state(
            plan_id=plan.id,
            stage_id="stage_01",
            role=role,
            target=S.WAITING_USER,
            review_kind=ReviewKind.PLAN_REVIEW,
        ),
    }
    before = store.get_plan(plan.id).revision
    with pytest.raises(RoleDenied) as err:
        calls[action]()
    assert err.value.code == "role_denied"
    assert store.get_plan(plan.id).revision == before


def test_full_progression_and_the_halves_each_role_owns() -> None:
    store = PlanStore()
    plan = store.create_plan(chat_id="c1", objective="o", role="orchestrator")
    store.write_stage_contract(plan_id=plan.id, role="planner", contract=contract())
    store.update_stage_state(
        plan_id=plan.id,
        stage_id="stage_01",
        role="orchestrator",
        target=S.WAITING_USER,
        review_kind=ReviewKind.PLAN_REVIEW,
        questions=["tone?"],
    )
    _, stage = store.get_stage(plan.id, "stage_01")
    assert stage.questions == ["tone?"]

    with pytest.raises(RoleDenied, match="only be attached while doing"):
        store.attach_runtime(plan_id=plan.id, stage_id="stage_01", role="executor", refs=[ref()])

    store.update_stage_state(
        plan_id=plan.id,
        stage_id="stage_01",
        role="orchestrator",
        target=S.DOING,
        user_confirmed=True,
    )
    with pytest.raises(RoleDenied, match="contract is frozen"):
        store.write_stage_contract(
            plan_id=plan.id, role="planner", stage_id="stage_01", contract=contract("changed")
        )

    store.attach_runtime(plan_id=plan.id, stage_id="stage_01", role="executor", refs=[ref()])
    _, stage = store.attach_runtime(
        plan_id=plan.id, stage_id="stage_01", role="executor", refs=[ref(), ref("outline_0002")]
    )
    assert [r.ref_id for r in stage.runtime.refs] == ["outline_0001", "outline_0002"]
    assert stage.runtime.attempts == 2
    assert stage.pending_items == []
    assert stage.questions == []

    plan, stage = store.update_stage_state(
        plan_id=plan.id, stage_id="stage_01", role="orchestrator", target=S.DONE
    )
    assert stage.state == S.DONE
    assert plan.revision == 6


def test_plans_survive_a_new_store_on_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "plans.db"
    plan = PlanStore(path).create_plan(chat_id="c9", objective="o", role="orchestrator")
    PlanStore(path).write_stage_contract(plan_id=plan.id, role="planner", contract=contract())

    reopened = PlanStore(path)
    latest = reopened.latest_for_chat("c9")
    assert latest is not None
    assert latest.revision == 1
    assert latest.stages[0].contract == contract()
    assert reopened.latest_for_chat("other") is None


def test_unknown_plan_and_stage_are_not_found_with_known_ids_in_the_hint() -> None:
    store = PlanStore()
    with pytest.raises(PlanNotFound):
        store.get_plan("plan_9999")
    plan = store.create_plan(chat_id="c1", objective="o", role="orchestrator")
    store.write_stage_contract(plan_id=plan.id, role="planner", contract=contract())
    with pytest.raises(PlanNotFound) as err:
        store.get_stage(plan.id, "stage_09")
    assert "stage_01" in (err.value.hint or "")
