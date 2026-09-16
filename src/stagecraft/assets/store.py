"""AssetStore: register by source, archive as a final record, resolve through one exit.

Every tool that takes an asset *name* goes through :meth:`AssetStore.resolve`. That is
the single place an archived asset is refused, so no tool can forget the rule, and
the refusal always reads the same way.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from stagecraft.assets.errors import AssetArchived, AssetNotFound
from stagecraft.assets.model import (
    ArchiveOutcome,
    ArchiveReason,
    ArchiveRecord,
    Asset,
    NewAsset,
    RegisterOutcome,
    TurnAssetChanges,
)
from stagecraft.db import Database, as_database
from stagecraft.plan import TERMINAL, PlanStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id                     TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL,
    name                   TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    source_id              TEXT NOT NULL,
    summary                TEXT NOT NULL,
    ref_id                 TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    registered_by_message  TEXT,
    archived_at            TEXT,
    archive_reason         TEXT,
    archive_message_id     TEXT,
    archive_note           TEXT
);
-- One active entry per source, and names the model can use unambiguously.
CREATE UNIQUE INDEX IF NOT EXISTS assets_active_source
    ON assets (session_id, source_id) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS assets_active_name
    ON assets (session_id, name) WHERE archived_at IS NULL;
-- Archiving is final, and nothing is ever deleted: enforced below the application.
CREATE TRIGGER IF NOT EXISTS assets_archive_is_final
    BEFORE UPDATE OF archived_at ON assets WHEN OLD.archived_at IS NOT NULL
    BEGIN SELECT RAISE(ABORT, 'archived assets cannot be restored'); END;
CREATE TRIGGER IF NOT EXISTS assets_are_never_deleted
    BEFORE DELETE ON assets
    BEGIN SELECT RAISE(ABORT, 'assets are archived, never deleted'); END;
"""

_COLUMNS = (
    "id, session_id, name, kind, source_id, summary, ref_id, created_at, updated_at, "
    "registered_by_message, archived_at, archive_reason, archive_message_id, archive_note"
)


class AssetStore:
    def __init__(self, db: Database | str | Path = ":memory:", plans: PlanStore | None = None):
        self.db = as_database(db)
        self.db.executescript(_SCHEMA)
        self.plans = plans

    # -- reads -----------------------------------------------------------------

    def active(self, session_id: str) -> list[Asset]:
        return self._select("session_id = ? AND archived_at IS NULL", (session_id,))

    def archived(self, session_id: str) -> list[Asset]:
        return self._select("session_id = ? AND archived_at IS NOT NULL", (session_id,))

    def resolve(self, session_id: str, name: str) -> Asset:
        """The one exit for looking an asset up by name (or id). Refuses archived assets."""
        (asset,) = self.resolve_many(session_id, [name])
        return asset

    def resolve_many(self, session_id: str, names: Iterable[str]) -> list[Asset]:
        found: list[Asset] = []
        archived: list[str] = []
        missing: list[str] = []
        for name in names:
            active = self._select(
                "session_id = ? AND (name = ? OR id = ?) AND archived_at IS NULL",
                (session_id, name, name),
            )
            if active:
                found.append(active[0])
                continue
            gone = self._select(
                "session_id = ? AND (name = ? OR id = ?) AND archived_at IS NOT NULL "
                "ORDER BY archived_at DESC",
                (session_id, name, name),
            )
            if gone and gone[0].archive is not None:
                record = gone[0].archive
                archived.append(f"{name}: archived at {record.archived_at} ({record.reason})")
            else:
                missing.append(name)
        if archived:
            raise AssetArchived(
                f"{len(archived)} asset(s) are archived and cannot be used",
                issues=archived,
                hint=(
                    "Archived assets cannot be used or restored. Tell the user and agree on a "
                    "replacement from the active assets."
                ),
            )
        if missing:
            known = ", ".join(a.name for a in self.active(session_id)) or "none"
            raise AssetNotFound(
                f"no active asset named {', '.join(missing)}",
                issues=missing,
                hint=f"Active assets: {known}.",
            )
        return found

    def dependents(self, session_id: str, assets: Iterable[Asset]) -> dict[str, list[str]]:
        """Stages that are not finished and whose contract names one of ``assets``."""
        plan = self.plans.latest_for_chat(session_id) if self.plans else None
        if plan is None:
            return {}
        result: dict[str, list[str]] = {}
        for asset in assets:
            stages = [
                stage.id
                for stage in plan.ordered()
                if stage.state not in TERMINAL
                and stage.contract is not None
                and {asset.name, asset.id} & set(stage.contract.assets)
            ]
            if stages:
                result[asset.name] = stages
        return result

    # -- writes ----------------------------------------------------------------

    def register(
        self, session_id: str, new: NewAsset, *, message_id: str | None = None
    ) -> RegisterOutcome:
        now = _now()
        with self.db.transaction() as conn:
            existing = self._select(
                "session_id = ? AND source_id = ? AND archived_at IS NULL",
                (session_id, new.source_id),
            )
            if existing:
                asset = existing[0]
                conn.execute(
                    "UPDATE assets SET kind = ?, summary = ?, ref_id = COALESCE(?, ref_id), "
                    "updated_at = ? WHERE id = ?",
                    (new.kind, new.summary, new.ref_id, now, asset.id),
                )
                return RegisterOutcome(self._get(asset.id), created=False)

            count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            asset_id = f"a_{count + 1:04d}"
            conn.execute(
                f"INSERT INTO assets ({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                (
                    asset_id,
                    session_id,
                    self._free_name(conn, session_id, new.name),
                    new.kind,
                    new.source_id,
                    new.summary,
                    new.ref_id,
                    now,
                    now,
                    message_id,
                ),
            )
        return RegisterOutcome(self._get(asset_id), created=True)

    def archive(
        self,
        session_id: str,
        names: Iterable[str],
        *,
        reason: ArchiveReason,
        message_id: str | None = None,
        note: str | None = None,
    ) -> ArchiveOutcome:
        """Archive by name or id. Already-archived targets are reported, not an error."""
        archived: list[Asset] = []
        already: list[Asset] = []
        now = _now()
        with self.db.transaction() as conn:
            for name in names:
                target = self._find_any(session_id, name)
                if target is None:
                    known = ", ".join(a.name for a in self.active(session_id)) or "none"
                    raise AssetNotFound(f"no asset named {name!r}", hint=f"Active assets: {known}.")
                if not target.active:
                    already.append(target)
                    continue
                conn.execute(
                    "UPDATE assets SET archived_at = ?, archive_reason = ?, "
                    "archive_message_id = ?, archive_note = ?, updated_at = ? WHERE id = ?",
                    (now, reason.value, message_id, note, now, target.id),
                )
                archived.append(self._get(target.id))
        return ArchiveOutcome(
            archived=archived,
            already_archived=already,
            dependent_stages=self.dependents(session_id, archived),
        )

    def apply_turn_changes(
        self,
        session_id: str,
        *,
        message_id: str | None,
        register: Iterable[NewAsset] = (),
        archive: Iterable[str] = (),
        archive_reason: ArchiveReason = ArchiveReason.PANEL,
    ) -> TurnAssetChanges:
        """This turn's uploads and archives: all of it lands, or none of it does."""
        with self.db.transaction():
            registered = [self.register(session_id, new, message_id=message_id) for new in register]
            outcome = self.archive(
                session_id, list(archive), reason=archive_reason, message_id=message_id
            )
        return TurnAssetChanges(message_id=message_id, registered=registered, archive=outcome)

    # -- internals -------------------------------------------------------------

    def _find_any(self, session_id: str, name: str) -> Asset | None:
        rows = self._select(
            "session_id = ? AND (name = ? OR id = ?) "
            "ORDER BY archived_at IS NOT NULL, archived_at DESC",
            (session_id, name, name),
        )
        return rows[0] if rows else None

    def _free_name(self, conn: sqlite3.Connection, session_id: str, wanted: str) -> str:
        name, n = wanted, 1
        while conn.execute(
            "SELECT 1 FROM assets WHERE session_id = ? AND name = ? AND archived_at IS NULL",
            (session_id, name),
        ).fetchone():
            n += 1
            name = f"{wanted} ({n})"
        return name

    def _get(self, asset_id: str) -> Asset:
        (asset,) = self._select("id = ?", (asset_id,))
        return asset

    def _select(self, where: str, params: tuple[object, ...]) -> list[Asset]:
        order = "" if "ORDER BY" in where else " ORDER BY created_at, id"
        rows = self.db.execute(f"SELECT {_COLUMNS} FROM assets WHERE {where}{order}", params)
        return [_asset(row) for row in rows.fetchall()]


def _asset(row: tuple[object, ...]) -> Asset:
    (
        asset_id,
        session_id,
        name,
        kind,
        source_id,
        summary,
        ref_id,
        created_at,
        updated_at,
        registered_by,
        archived_at,
        reason,
        archive_message,
        note,
    ) = row
    record = None
    if archived_at is not None:
        record = ArchiveRecord(
            archived_at=str(archived_at),
            reason=ArchiveReason(str(reason)),
            message_id=archive_message,  # type: ignore[arg-type]
            note=note,  # type: ignore[arg-type]
        )
    return Asset(
        id=str(asset_id),
        session_id=str(session_id),
        name=str(name),
        kind=str(kind),
        source_id=str(source_id),
        summary=str(summary),
        ref_id=ref_id,  # type: ignore[arg-type]
        created_at=str(created_at),
        updated_at=str(updated_at),
        registered_by_message=registered_by,  # type: ignore[arg-type]
        archive=record,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
