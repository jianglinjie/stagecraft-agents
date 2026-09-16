"""Long-term memory: one profile per topic, rewritten whole after a session.

Appending notes grows forever and keeps contradictions side by side ("prefers formal
tone" next to "switched to playful"). Here, a finished session's transcript and the
topic's current profile go to a rewriter that returns the *whole* new profile: what
still holds, plus what this session taught, minus what it contradicted, under a size
cap.

Two sessions on the same topic can finish at once. The write is a compare-and-swap
on the profile's revision; the loser re-reads the winner's profile and rewrites
from that, so neither session's lessons are silently overwritten.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from agents import Agent, Model, RunConfig, Runner
from pydantic import BaseModel

from stagecraft.db import Database
from stagecraft.memory.items import render_transcript

Rewriter = Callable[[str, str], Awaitable[str]]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_profiles (
    topic       TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

REWRITER_INSTRUCTIONS = """\
You maintain a short profile of what has been learned about one topic across content-production
sessions: audience, tone, formats that worked, things users rejected, constraints.

You get the current profile and a finished session's transcript. Return the complete new
profile: keep what still holds, add what this session taught, remove what it contradicted.
Only durable preferences and facts, never one-off details. Plain bullet lines, under 150 words.
Output only the profile."""


class MemoryProfile(BaseModel):
    topic: str
    content: str
    revision: int
    updated_at: str


class MemoryConflict(RuntimeError):
    pass


class LongTermMemory:
    def __init__(self, db: Database, *, max_chars: int = 2000) -> None:
        self.db = db
        self.max_chars = max_chars
        db.executescript(_SCHEMA)

    def get(self, topic: str) -> MemoryProfile | None:
        row = self.db.execute(
            "SELECT topic, content, revision, updated_at FROM memory_profiles WHERE topic = ?",
            (topic,),
        ).fetchone()
        return (
            None
            if row is None
            else MemoryProfile(topic=row[0], content=row[1], revision=row[2], updated_at=row[3])
        )

    async def extract(
        self,
        topic: str,
        items: Sequence[Any],
        rewriter: Rewriter,
        *,
        attempts: int = 3,
    ) -> MemoryProfile:
        transcript = render_transcript(items)
        for _ in range(attempts):
            current = self.get(topic)
            content = (await rewriter(current.content if current else "", transcript)).strip()
            content = content[: self.max_chars]
            if self._write(topic, content, current.revision if current else None):
                profile = self.get(topic)
                assert profile is not None
                return profile
        raise MemoryConflict(f"profile {topic!r} kept changing; gave up after {attempts} tries")

    def _write(self, topic: str, content: str, expected_revision: int | None) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.db.transaction() as conn:
            if expected_revision is None:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO memory_profiles (topic, content, revision, updated_at) "
                    "VALUES (?, ?, 1, ?)",
                    (topic, content, now),
                )
            else:
                cursor = conn.execute(
                    "UPDATE memory_profiles SET content = ?, revision = revision + 1, "
                    "updated_at = ? WHERE topic = ? AND revision = ?",
                    (content, now, topic, expected_revision),
                )
        return cursor.rowcount == 1


class ModelRewriter:
    def __init__(self, model: Model) -> None:
        self.model = model

    async def __call__(self, current: str, transcript: str) -> str:
        agent = Agent(name="memory-rewriter", instructions=REWRITER_INSTRUCTIONS, model=self.model)
        prompt = f"Current profile:\n{current or '(empty)'}\n\nSession transcript:\n{transcript}"
        result = await Runner.run(
            agent, prompt, max_turns=1, run_config=RunConfig(tracing_disabled=True)
        )
        return str(result.final_output or "")
