"""An in-memory stand-in for the database and object storage behind real tools.

Tools put their raw artefacts here and hand the model only an id and a summary.
Later tools fetch the artefact back by id. Ids are deterministic (``brief_0001``)
so tests and scripted models can refer to them.
"""

from __future__ import annotations

from typing import Any


class FakeWorkspace:
    def __init__(self, *, render_delay_seconds: float = 1.0) -> None:
        self.render_delay_seconds = render_delay_seconds
        self._records: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {}

    def put(self, kind: str, payload: dict[str, Any]) -> str:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        record_id = f"{kind}_{self._counters[kind]:04d}"
        self._records[record_id] = {"kind": kind, **payload}
        return record_id

    def require(self, record_id: str, kind: str) -> dict[str, Any]:
        record = self._records.get(record_id)
        if record is None or record["kind"] != kind:
            raise LookupError(f"no {kind} with id {record_id!r}")
        return record

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """A copy of every record, for asserting on what a run produced."""
        return {rid: dict(rec) for rid, rec in self._records.items()}

    def ids(self, kind: str | None = None) -> list[str]:
        return [rid for rid, rec in self._records.items() if kind is None or rec["kind"] == kind]

    def __len__(self) -> int:
        return len(self._records)
