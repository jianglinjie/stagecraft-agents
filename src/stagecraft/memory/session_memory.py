"""SessionMemory: the SDK ``Session`` interface over SQLite, with repair and compaction.

The SDK reads a session at the start of a run and appends to it at the end. Here:

* **reads** go through :func:`~stagecraft.memory.items.repair_history`, so a renamed
  tool or a call cut off by a crash cannot break the next turn;
* **writes** append rows ordered by ``seq``;
* **compaction** is an explicit step before a run. When the history's estimated size
  passes the threshold, the old part is replaced by a summary in one transaction.
  If the summariser fails, nothing changes, the run goes ahead with the full
  history, and the next run tries again.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from agents.memory import SessionABC

from stagecraft.db import Database
from stagecraft.memory.compaction import SUMMARY_PREFIX, Compactor, find_cut
from stagecraft.memory.items import RepairReport, estimate_tokens, repair_history

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    item        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_items_order ON memory_items (session_id, seq);
CREATE TABLE IF NOT EXISTS memory_compactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    replaced_items   INTEGER NOT NULL,
    tokens_before    INTEGER NOT NULL,
    tokens_after     INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class CompactionOutcome:
    status: Literal["skipped", "compacted", "failed"]
    reason: str = ""
    replaced_items: int = 0
    tokens_before: int = 0
    tokens_after: int = 0


class SessionMemory(SessionABC):
    session_settings = None

    def __init__(
        self,
        session_id: str,
        db: Database,
        *,
        known_tools: Iterable[str] | None = None,
        threshold_tokens: int = 8000,
        keep_recent_user_turns: int = 2,
    ) -> None:
        self.session_id = session_id
        self.db = db
        self.known_tools = set(known_tools) if known_tools is not None else None
        self.threshold_tokens = threshold_tokens
        self.keep_recent_user_turns = keep_recent_user_turns
        self.last_repair = RepairReport()
        self._warned: set[str] = set()
        db.executescript(_SCHEMA)

    # -- Session interface ---------------------------------------------------------

    async def get_items(self, limit: int | None = None) -> list[Any]:
        _, items = self._load()
        repaired, report = repair_history(items, self.known_tools)
        self.last_repair = report
        self._warn(report)
        return repaired[-limit:] if limit else repaired

    async def add_items(self, items: list[Any]) -> None:
        if not items:
            return
        now = _now()
        with self.db.transaction() as conn:
            (top,) = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM memory_items WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            conn.executemany(
                "INSERT INTO memory_items (session_id, seq, item, created_at) VALUES (?, ?, ?, ?)",
                [
                    (
                        self.session_id,
                        top + i,
                        json.dumps(item, ensure_ascii=False, default=str),
                        now,
                    )
                    for i, item in enumerate(items, start=1)
                ],
            )

    async def pop_item(self) -> Any | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id, item FROM memory_items WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM memory_items WHERE id = ?", (row[0],))
        return json.loads(row[1])

    async def clear_session(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM memory_items WHERE session_id = ?", (self.session_id,))

    # -- compaction ------------------------------------------------------------------

    async def maybe_compact(self, compactor: Compactor | None) -> CompactionOutcome:
        if compactor is None:
            return CompactionOutcome("skipped", "no compactor configured")
        seqs, items = self._load()
        before = estimate_tokens(items)
        if before <= self.threshold_tokens:
            return CompactionOutcome("skipped", "under threshold", tokens_before=before)
        cut = find_cut(items, keep_recent_user_turns=self.keep_recent_user_turns)
        if cut == 0:
            return CompactionOutcome(
                "skipped", "nothing older than the kept turns", tokens_before=before
            )

        head, cut_seq = items[:cut], seqs[cut - 1]
        try:
            summary = await compactor(repair_history(head, self.known_tools)[0])
        except Exception as err:  # noqa: BLE001 - a failed summary must never fail the turn
            log.warning("compaction of %s failed, keeping full history: %s", self.session_id, err)
            return CompactionOutcome("failed", str(err), tokens_before=before)

        summary_item = {"role": "assistant", "content": SUMMARY_PREFIX + summary}
        after = estimate_tokens([summary_item, *items[cut:]])
        with self.db.transaction() as conn:
            (still_there,) = conn.execute(
                "SELECT COUNT(*) FROM memory_items WHERE session_id = ? AND seq <= ?",
                (self.session_id, cut_seq),
            ).fetchone()
            if still_there != cut:
                return CompactionOutcome(
                    "skipped", "history changed during compaction", tokens_before=before
                )
            conn.execute(
                "DELETE FROM memory_items WHERE session_id = ? AND seq <= ?",
                (self.session_id, cut_seq),
            )
            conn.execute(
                "INSERT INTO memory_items (session_id, seq, item, created_at) VALUES (?, ?, ?, ?)",
                (self.session_id, cut_seq, json.dumps(summary_item, ensure_ascii=False), _now()),
            )
            conn.execute(
                "INSERT INTO memory_compactions "
                "(session_id, replaced_items, tokens_before, tokens_after, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.session_id, cut, before, after, _now()),
            )
        return CompactionOutcome(
            "compacted", replaced_items=cut, tokens_before=before, tokens_after=after
        )

    def compactions(self) -> int:
        (count,) = self.db.execute(
            "SELECT COUNT(*) FROM memory_compactions WHERE session_id = ?", (self.session_id,)
        ).fetchone()
        return int(count)

    # -- internals -------------------------------------------------------------------

    def _load(self) -> tuple[list[int], list[Any]]:
        rows = self.db.execute(
            "SELECT seq, item FROM memory_items WHERE session_id = ? ORDER BY seq",
            (self.session_id,),
        ).fetchall()
        return [row[0] for row in rows], [json.loads(row[1]) for row in rows]

    def _warn(self, report: RepairReport) -> None:
        for name in sorted(report.retired_tools - self._warned):
            self._warned.add(name)
            log.warning("session %s: history calls retired tool %s", self.session_id, name)
        if report.interrupted_calls:
            key = "interrupted:" + ",".join(report.interrupted_calls)
            if key not in self._warned:
                self._warned.add(key)
                log.warning(
                    "session %s: %d tool call(s) never returned",
                    self.session_id,
                    len(report.interrupted_calls),
                )


def _now() -> str:
    return datetime.now(UTC).isoformat()
