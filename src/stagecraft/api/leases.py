"""Turn leases: at most one running turn per session, and none held forever.

A lease is a lock with an expiry. The holder refreshes it while working; if the
process dies, refreshing stops and the lease simply runs out. The random token
makes refresh and release conditional on still being the holder, so a process that
wakes up after its lease was taken over cannot extend or delete the new holder's.

Acquire is one statement, the SQLite equivalent of ``SET key token NX PX ttl``::

    INSERT ... ON CONFLICT(session_id) DO UPDATE SET token = ?, expires_at = ?
    WHERE leases.expires_at <= now

A live lease makes the ``WHERE`` false and ``rowcount`` 0: refused. An expired one is
overwritten: taken over.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class LeaseHeld(Exception):
    """Somebody else holds a live lease on this session."""


@dataclass(frozen=True)
class Lease:
    session_id: str
    token: str
    expires_at: float


class LeaseStore:
    def __init__(
        self, path: str | Path = ":memory:", *, clock: Callable[[], float] = time.time
    ) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS leases ("
            "session_id TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        self._lock = threading.Lock()
        self.clock = clock

    def acquire(self, session_id: str, ttl: float) -> Lease:
        now = self.clock()
        lease = Lease(session_id=session_id, token=uuid.uuid4().hex, expires_at=now + ttl)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO leases (session_id, token, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "token = excluded.token, expires_at = excluded.expires_at "
                "WHERE leases.expires_at <= ?",
                (lease.session_id, lease.token, lease.expires_at, now),
            )
        if cursor.rowcount != 1:
            raise LeaseHeld(session_id)
        return lease

    def refresh(self, lease: Lease, ttl: float) -> Lease | None:
        """Extend a lease this caller still holds. ``None`` means it was lost."""
        now = self.clock()
        renewed = Lease(session_id=lease.session_id, token=lease.token, expires_at=now + ttl)
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE leases SET expires_at = ? "
                "WHERE session_id = ? AND token = ? AND expires_at > ?",
                (renewed.expires_at, lease.session_id, lease.token, now),
            )
        return renewed if cursor.rowcount == 1 else None

    def release(self, lease: Lease) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM leases WHERE session_id = ? AND token = ?",
                (lease.session_id, lease.token),
            )
        return cursor.rowcount == 1

    def holder(self, session_id: str) -> Lease | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT token, expires_at FROM leases WHERE session_id = ? AND expires_at > ?",
                (session_id, self.clock()),
            ).fetchone()
        return None if row is None else Lease(session_id, row[0], row[1])
