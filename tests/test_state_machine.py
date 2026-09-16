import pytest

from stagecraft.plan import (
    TERMINAL,
    TRANSITIONS,
    ConfirmationRequired,
    ContractRequired,
    IllegalTransition,
    ResultsRequired,
    ReviewKind,
    RuntimeRef,
    Stage,
    StageContract,
    StageState,
    WorkItem,
    check_transition,
    next_action,
)

S = StageState
ALL = list(StageState)


def make_stage(
    state: StageState,
    *,
    contract: bool = True,
    refs: bool = True,
    review_kind: ReviewKind | None = None,
) -> Stage:
    stage = Stage(id="stage_01", order=1, goal="g", state=state, review_kind=review_kind)
    if contract:
        stage.contract = StageContract(
            goal="g", work_items=[WorkItem(id="w1", name="n", instruction="i")]
        )
    if refs:
        stage.runtime.refs.append(
            RuntimeRef(ref_id="draft_0001", kind="draft", summary="s", work_item_id="w1")
        )
    return stage


@pytest.mark.parametrize("target", ALL)
@pytest.mark.parametrize("current", ALL)
def test_transition_table_is_exactly_what_is_enforced(
    current: StageState, target: StageState
) -> None:
    review = ReviewKind.RESULT_REVIEW if current == S.WAITING_USER else None
    stage = make_stage(current, review_kind=review)
    kind = None
    if target == S.WAITING_USER:
        kind = ReviewKind.PLAN_REVIEW if current == S.PENDING else ReviewKind.RESULT_REVIEW

    if target in TRANSITIONS[current]:
        check_transition(stage, target, user_confirmed=True, review_kind=kind)
    else:
        with pytest.raises(IllegalTransition):
            check_transition(stage, target, user_confirmed=True, review_kind=kind)


def test_there_is_no_edge_from_pending_to_doing() -> None:
    with pytest.raises(IllegalTransition, match="pending -> doing"):
        check_transition(make_stage(S.PENDING), S.DOING, user_confirmed=True)


def test_leaving_review_needs_explicit_confirmation() -> None:
    stage = make_stage(S.WAITING_USER, review_kind=ReviewKind.PLAN_REVIEW)
    with pytest.raises(ConfirmationRequired) as err:
        check_transition(stage, S.DOING)
    assert err.value.code == "confirmation_required"
    assert "user_confirmed=true" in (err.value.hint or "")
    check_transition(stage, S.DOING, user_confirmed=True)

    result_review = make_stage(S.WAITING_USER, review_kind=ReviewKind.RESULT_REVIEW)
    with pytest.raises(ConfirmationRequired):
        check_transition(result_review, S.DONE)


def test_plan_review_cannot_jump_to_done() -> None:
    stage = make_stage(S.WAITING_USER, review_kind=ReviewKind.PLAN_REVIEW)
    with pytest.raises(IllegalTransition, match="has not been executed"):
        check_transition(stage, S.DONE, user_confirmed=True)


def test_review_kind_must_match_where_the_stage_comes_from() -> None:
    with pytest.raises(IllegalTransition, match="plan_review"):
        check_transition(
            make_stage(S.PENDING), S.WAITING_USER, review_kind=ReviewKind.RESULT_REVIEW
        )
    with pytest.raises(IllegalTransition, match="result_review"):
        check_transition(make_stage(S.DOING), S.WAITING_USER, review_kind=ReviewKind.PLAN_REVIEW)
    with pytest.raises(IllegalTransition):
        check_transition(make_stage(S.PENDING), S.WAITING_USER, review_kind=None)


def test_no_review_or_execution_without_a_contract() -> None:
    with pytest.raises(ContractRequired):
        check_transition(
            make_stage(S.PENDING, contract=False),
            S.WAITING_USER,
            review_kind=ReviewKind.PLAN_REVIEW,
        )
    with pytest.raises(ContractRequired):
        check_transition(make_stage(S.BLOCKED, contract=False), S.DOING)


def test_nothing_produced_means_not_done() -> None:
    with pytest.raises(ResultsRequired):
        check_transition(make_stage(S.DOING, refs=False), S.DONE)
    with pytest.raises(ResultsRequired):
        check_transition(
            make_stage(S.DOING, refs=False), S.WAITING_USER, review_kind=ReviewKind.RESULT_REVIEW
        )


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_states_accept_nothing(terminal: StageState) -> None:
    assert TRANSITIONS[terminal] == frozenset()
    for target in ALL:
        with pytest.raises(IllegalTransition):
            check_transition(make_stage(terminal), target, user_confirmed=True)


def test_next_action_names_the_step_for_every_state() -> None:
    assert "dispatch_planner" in next_action(make_stage(S.PENDING, contract=False))
    assert "plan_review" in next_action(make_stage(S.PENDING))
    assert "end the turn" in next_action(
        make_stage(S.WAITING_USER, review_kind=ReviewKind.PLAN_REVIEW)
    )
    assert "dispatch_executor" in next_action(make_stage(S.DOING))
    blocked = make_stage(S.BLOCKED)
    blocked.blocked_reason = "vendor down"
    assert "vendor down" in next_action(blocked)
    assert "next pending" in next_action(make_stage(S.DONE))
    assert "omitted" in next_action(make_stage(S.OMITTED))
