"""Sessions, messages and turns: what the HTTP layer persists before any agent runs.

A user message and the turn it starts are written in one transaction, before the
request returns. The run happens afterwards, in the background. If the run dies,
the message is still there and the turn row says how it ended.

``client_message_id`` is unique per session. A client that never saw the response
to its send can send again with the same id and get the original turn back instead
of starting a second one.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from stagecraft.db import Database, as_database

TurnStatus = Literal["running", "completed", "failed"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    topic       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(id),
    role               TEXT NOT NULL,
    content            TEXT NOT NULL,
    client_message_id  TEXT,
    turn_id            TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE (session_id, client_message_id)
);
CREATE TABLE IF NOT EXISTS turns (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    message_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    output       TEXT,
    error        TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);
"""


@dataclass(frozen=True)
class SessionRecord:
    id: str
    title: str | None
    topic: str | None
    created_at: str


@dataclass(frozen=True)
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str
    client_message_id: str | None
    turn_id: str | None
    created_at: str


@dataclass(frozen=True)
class TurnRecord:
    id: str
    session_id: str
    message_id: str
    status: TurnStatus
    output: str | None
    error: str | None
    started_at: str
    finished_at: str | None


class DuplicateClientMessage(Exception):
    def __init__(self, existing: MessageRecord) -> None:
        super().__init__(existing.client_message_id)
        self.existing = existing


class SessionStore:
    def __init__(self, db: Database | str | Path = ":memory:") -> None:
        self.db = as_database(db)
        self.db.executescript(_SCHEMA)
        self._conn = self.db.conn
        self._lock = self.db.lock

    def create_session(self, title: str | None = None, topic: str | None = None) -> SessionRecord:
        record = SessionRecord(
            id=f"s_{uuid.uuid4().hex[:12]}", title=title, topic=topic, created_at=_now()
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, title, topic, created_at) VALUES (?, ?, ?, ?)",
                (record.id, record.title, record.topic, record.created_at),
            )
        return record

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, topic, created_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return None if row is None else SessionRecord(*row)

    def list_sessions(self, limit: int = 100) -> list[SessionRecord]:
        """Newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, topic, created_at FROM sessions "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [SessionRecord(*row) for row in rows]

    def message_by_client_id(self, session_id: str, client_message_id: str) -> MessageRecord | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages "
                "WHERE session_id = ? AND client_message_id = ?",
                (session_id, client_message_id),
            ).fetchone()
        return None if row is None else MessageRecord(*row)

    def record_user_message(
        self, session_id: str, content: str, client_message_id: str | None
    ) -> tuple[MessageRecord, TurnRecord]:
        """The message and its running turn, in one transaction."""
        now = _now()
        message = MessageRecord(
            id=f"m_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            role="user",
            content=content,
            client_message_id=client_message_id,
            turn_id=f"t_{uuid.uuid4().hex[:12]}",
            created_at=now,
        )
        turn = TurnRecord(
            id=message.turn_id or "",
            session_id=session_id,
            message_id=message.id,
            status="running",
            output=None,
            error=None,
            started_at=now,
            finished_at=None,
        )
        with self.db.transaction():
            try:
                self._insert_message(message)
            except sqlite3.IntegrityError:
                existing = self.message_by_client_id(session_id, client_message_id or "")
                if existing is None:
                    raise
                raise DuplicateClientMessage(existing) from None
            self._conn.execute(
                "INSERT INTO turns (id, session_id, message_id, status, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (turn.id, session_id, message.id, turn.status, turn.started_at),
            )
        return message, turn

    def add_assistant_message(self, session_id: str, turn_id: str, content: str) -> MessageRecord:
        message = MessageRecord(
            id=f"m_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            role="assistant",
            content=content,
            client_message_id=None,
            turn_id=turn_id,
            created_at=_now(),
        )
        with self._lock:
            self._insert_message(message)
        return message

    def finish_turn(
        self,
        turn_id: str,
        status: TurnStatus,
        *,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE turns SET status = ?, output = ?, error = ?, finished_at = ? "
                "WHERE id = ? AND status = 'running'",
                (status, output, error, _now(), turn_id),
            )

    def messages(self, session_id: str) -> list[MessageRecord]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE session_id = ? "
                "ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
        return [MessageRecord(*row) for row in rows]

    def turns(self, session_id: str) -> list[TurnRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, message_id, status, output, error, started_at, finished_at "
                "FROM turns WHERE session_id = ? ORDER BY started_at, rowid",
                (session_id,),
            ).fetchall()
        return [TurnRecord(*row) for row in rows]

    def _insert_message(self, message: MessageRecord) -> None:
        self._conn.execute(
            f"INSERT INTO messages ({_MESSAGE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message.id,
                message.session_id,
                message.role,
                message.content,
                message.client_message_id,
                message.turn_id,
                message.created_at,
            ),
        )


_MESSAGE_COLUMNS = "id, session_id, role, content, client_message_id, turn_id, created_at"


def _now() -> str:
    return datetime.now(UTC).isoformat()
