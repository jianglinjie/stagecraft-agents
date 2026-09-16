import sqlite3

import pytest

from stagecraft.assets import (
    ArchiveReason,
    AssetArchived,
    AssetNotFound,
    AssetStore,
    NewAsset,
)
from stagecraft.db import Database
from stagecraft.plan import PlanStore, ReviewKind, StageContract, StageState


def new(name: str, source: str, kind: str = "image", summary: str = "") -> NewAsset:
    return NewAsset(name=name, kind=kind, source_id=source, summary=summary)


def test_the_same_source_updates_its_entry_instead_of_adding_one() -> None:
    store = AssetStore()
    first = store.register("s1", new("hero", "https://x/p/1#hero", summary="v1"), message_id="m1")
    again = store.register("s1", new("hero shot", "https://x/p/1#hero", summary="v2"))

    assert first.created is True and again.created is False
    assert again.asset.id == first.asset.id
    assert again.asset.name == "hero"
    assert again.asset.summary == "v2"
    assert [a.id for a in store.active("s1")] == [first.asset.id]
    assert store.register("s2", new("hero", "https://x/p/1#hero")).created is True


def test_names_stay_unambiguous() -> None:
    store = AssetStore()
    store.register("s1", new("logo", "a"))
    second = store.register("s1", new("logo", "b"))
    third = store.register("s1", new("logo", "c"))
    assert (second.asset.name, third.asset.name) == ("logo (2)", "logo (3)")


def test_archive_is_a_record_and_the_asset_can_no_longer_be_used() -> None:
    store = AssetStore()
    asset = store.register("s1", new("hero", "src-1")).asset
    outcome = store.archive(
        "s1", ["hero"], reason=ArchiveReason.USER_REQUEST, message_id="m7", note="off-brand"
    )

    (archived,) = outcome.archived
    assert archived.id == asset.id
    assert archived.archive is not None
    assert archived.archive.reason == ArchiveReason.USER_REQUEST
    assert (archived.archive.message_id, archived.archive.note) == ("m7", "off-brand")
    assert store.active("s1") == []
    assert [a.id for a in store.archived("s1")] == [asset.id]

    with pytest.raises(AssetArchived) as err:
        store.resolve("s1", "hero")
    assert err.value.code == "asset_archived"
    assert "user_request" in err.value.issues[0]
    with pytest.raises(AssetArchived):
        store.resolve("s1", asset.id)


def test_archiving_twice_is_reported_not_an_error() -> None:
    store = AssetStore()
    store.register("s1", new("hero", "src-1"))
    store.archive("s1", ["hero"], reason=ArchiveReason.PANEL)
    again = store.archive("s1", ["hero"], reason=ArchiveReason.PANEL)
    assert again.archived == []
    assert [a.name for a in again.already_archived] == ["hero"]


def test_there_is_no_way_back_from_archive() -> None:
    db = Database()
    store = AssetStore(db)
    asset = store.register("s1", new("hero", "src-1")).asset
    store.archive("s1", ["hero"], reason=ArchiveReason.PANEL)

    with pytest.raises(sqlite3.IntegrityError, match="cannot be restored"):
        db.execute("UPDATE assets SET archived_at = NULL WHERE id = ?", (asset.id,))
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        db.execute("DELETE FROM assets WHERE id = ?", (asset.id,))

    comeback = store.register("s1", new("hero", "src-1"))
    assert comeback.created is True
    assert comeback.asset.id != asset.id
    assert store.archived("s1")[0].id == asset.id


def test_resolve_is_the_one_exit_and_lists_every_problem() -> None:
    store = AssetStore()
    store.register("s1", new("hero", "a"))
    store.register("s1", new("logo", "b"))
    store.register("s1", new("old", "c"))
    store.archive("s1", ["old"], reason=ArchiveReason.PANEL)

    assert [a.name for a in store.resolve_many("s1", ["logo", "hero"])] == ["logo", "hero"]
    with pytest.raises(AssetArchived):
        store.resolve_many("s1", ["hero", "old", "missing"])
    with pytest.raises(AssetNotFound) as err:
        store.resolve_many("s1", ["hero", "missing"])
    assert err.value.issues == ["missing"]
    assert "hero" in (err.value.hint or "") and "logo" in (err.value.hint or "")


def test_archive_reports_unfinished_stages_that_still_use_the_asset() -> None:
    plans = PlanStore()
    store = AssetStore(plans=plans)
    store.register("chat", new("hero", "a"))
    store.register("chat", new("logo", "b"))
    plan = plans.create_plan(chat_id="chat", objective="o", role="orchestrator")
    for goal, used in (("banner", ["hero"]), ("footer", ["logo"]), ("recap", ["hero"])):
        plans.write_stage_contract(
            plan_id=plan.id, role="planner", contract=StageContract(goal=goal, assets=used)
        )
    # stage_03 is finished: it no longer depends on anything.
    plans.update_stage_state(
        plan_id=plan.id,
        stage_id="stage_03",
        role="orchestrator",
        target=StageState.WAITING_USER,
        review_kind=ReviewKind.PLAN_REVIEW,
    )
    plans.update_stage_state(
        plan_id=plan.id, stage_id="stage_03", role="orchestrator", target=StageState.OMITTED
    )

    outcome = store.archive("chat", ["hero"], reason=ArchiveReason.PANEL)
    assert outcome.dependent_stages == {"hero": ["stage_01"]}


def test_turn_changes_land_together_or_not_at_all() -> None:
    store = AssetStore()
    store.register("s1", new("existing", "e"))

    with pytest.raises(AssetNotFound):
        store.apply_turn_changes(
            "s1",
            message_id="m1",
            register=[new("upload-1", "u1"), new("upload-2", "u2")],
            archive=["existing", "no-such-asset"],
        )
    assert [a.name for a in store.active("s1")] == ["existing"]
    assert store.archived("s1") == []

    changes = store.apply_turn_changes(
        "s1", message_id="m2", register=[new("upload-1", "u1")], archive=["existing"]
    )
    assert [r.asset.name for r in changes.registered] == ["upload-1"]
    assert changes.registered[0].asset.registered_by_message == "m2"
    assert [a.name for a in changes.archive.archived] == ["existing"]
    assert changes.archive.archived[0].archive.message_id == "m2"  # type: ignore[union-attr]
    assert not changes.empty
