from stagecraft.plan import PlanStore, ReviewKind, StageState
from stagecraft.tools import RunContext, ToolError, ToolRegistry
from stagecraft.tools.plan import PlanCreated, StageDetail, StageWritten, build_plan_tools


def registry_and_store() -> tuple[ToolRegistry, PlanStore]:
    store = PlanStore()
    return ToolRegistry(build_plan_tools(store)), store


def as_role(role: str) -> RunContext:
    return RunContext(role=role, chat_id="chat_1")  # type: ignore[arg-type]


WORK = [{"id": "w1", "name": "outline", "instruction": "three sections"}]


def test_the_role_is_not_in_any_schema() -> None:
    registry, _ = registry_and_store()
    for spec in registry:
        assert "ctx" not in spec.params_json_schema.get("properties", {})
        assert "role" not in spec.params_json_schema.get("properties", {})


async def test_writing_tools_need_a_run_context() -> None:
    registry, _ = registry_and_store()
    result = await registry.get("plan_create").invoke({"objective": "x"})
    assert isinstance(result, ToolError)
    assert "without a run context" in result.message


async def test_role_comes_from_the_context_and_the_store_enforces_it() -> None:
    registry, store = registry_and_store()
    created = await registry.get("plan_create").invoke(
        {"objective": "series"}, as_role("orchestrator")
    )
    assert isinstance(created, PlanCreated)
    assert created.plan_id == "plan_0001"
    assert store.get_plan("plan_0001").chat_id == "chat_1"

    denied = await registry.get("plan_write_stage_contract").invoke(
        {"plan_id": "plan_0001", "goal": "outline", "work_items": WORK}, as_role("executor")
    )
    assert isinstance(denied, ToolError)
    assert denied.code == "role_denied"

    written = await registry.get("plan_write_stage_contract").invoke(
        {"plan_id": "plan_0001", "goal": "outline", "work_items": WORK}, as_role("planner")
    )
    assert isinstance(written, StageWritten)
    assert (written.stage_id, written.revision, written.state) == ("stage_01", 1, "pending")
    assert "plan_review" in written.next_action
    assert "stage_01" in written.plan_summary


async def test_refusals_come_back_as_codes_with_hints() -> None:
    registry, _ = registry_and_store()
    await registry.get("plan_create").invoke({"objective": "o"}, as_role("orchestrator"))
    await registry.get("plan_write_stage_contract").invoke(
        {"plan_id": "plan_0001", "goal": "g", "work_items": WORK}, as_role("planner")
    )
    update = registry.get("plan_update_stage_state")

    skip = await update.invoke(
        {"plan_id": "plan_0001", "stage_id": "stage_01", "target": "doing", "user_confirmed": True},
        as_role("orchestrator"),
    )
    assert isinstance(skip, ToolError)
    assert skip.code == "illegal_transition"
    assert skip.hint is not None and "waiting_user" in skip.hint

    await update.invoke(
        {
            "plan_id": "plan_0001",
            "stage_id": "stage_01",
            "target": "waiting_user",
            "review_kind": "plan_review",
        },
        as_role("orchestrator"),
    )
    unconfirmed = await update.invoke(
        {"plan_id": "plan_0001", "stage_id": "stage_01", "target": "doing"},
        as_role("orchestrator"),
    )
    assert isinstance(unconfirmed, ToolError)
    assert unconfirmed.code == "confirmation_required"

    stale = await update.invoke(
        {
            "plan_id": "plan_0001",
            "stage_id": "stage_01",
            "target": "doing",
            "user_confirmed": True,
            "expected_revision": 0,
        },
        as_role("orchestrator"),
    )
    assert isinstance(stale, ToolError)
    assert stale.code == "revision_conflict"

    missing = await registry.get("plan_get_stage_detail").invoke(
        {"plan_id": "plan_0001", "stage_id": "stage_07"}
    )
    assert isinstance(missing, ToolError)
    assert missing.code == "not_found"
    assert "stage_01" in (missing.hint or "")


async def test_stage_detail_resolves_upstream_pointers() -> None:
    registry, store = registry_and_store()
    orchestrator, planner, executor = (
        as_role("orchestrator"),
        as_role("planner"),
        as_role("executor"),
    )
    await registry.get("plan_create").invoke({"objective": "o"}, orchestrator)
    write = registry.get("plan_write_stage_contract")
    await write.invoke({"plan_id": "plan_0001", "goal": "brief", "work_items": WORK}, planner)
    await write.invoke(
        {
            "plan_id": "plan_0001",
            "goal": "draft",
            "work_items": WORK,
            "inputs": ["stage_01", "stage_00"],
        },
        planner,
    )

    detail = await registry.get("plan_get_stage_detail").invoke(
        {"plan_id": "plan_0001", "stage_id": "stage_02"}
    )
    assert isinstance(detail, StageDetail)
    assert detail.upstream_refs == []
    assert detail.unresolved_inputs == ["stage_01", "stage_00"]
    assert detail.pending_items == ["w1"]

    store.update_stage_state(
        plan_id="plan_0001",
        stage_id="stage_01",
        role="orchestrator",
        target=StageState.WAITING_USER,
        review_kind=ReviewKind.PLAN_REVIEW,
    )
    store.update_stage_state(
        plan_id="plan_0001",
        stage_id="stage_01",
        role="orchestrator",
        target=StageState.DOING,
        user_confirmed=True,
    )
    attached = await registry.get("plan_attach_runtime").invoke(
        {
            "plan_id": "plan_0001",
            "stage_id": "stage_01",
            "refs": [
                {"ref_id": "brief_0001", "kind": "brief", "summary": "s", "work_item_id": "w1"}
            ],
        },
        executor,
    )
    assert isinstance(attached, StageWritten)
    assert attached.pending_items == []

    detail = await registry.get("plan_get_stage_detail").invoke(
        {"plan_id": "plan_0001", "stage_id": "stage_02"}
    )
    assert isinstance(detail, StageDetail)
    assert [r.ref_id for r in detail.upstream_refs] == ["brief_0001"]
    assert detail.unresolved_inputs == ["stage_00"]
