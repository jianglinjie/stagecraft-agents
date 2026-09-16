"""One SQLite connection, one lock, and transactions that compose.

Stores that must change together share a :class:`Database`. A method opens
``with db.transaction():`` whether or not a caller already did; only the outermost
block issues ``BEGIN IMMEDIATE`` / ``COMMIT``, and any exception rolls the whole thing
back. That is how "record the message, register this turn's uploads and archive what
the user archived" becomes one write that lands completely or not at all.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            outermost = self._depth == 0
            if outermost:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if outermost:
                    self.conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outermost:
                self.conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def executescript(self, sql: str) -> None:
        with self.lock:
            self.conn.executescript(sql)


def as_database(target: Database | str | Path) -> Database:
    return target if isinstance(target, Database) else Database(target)
