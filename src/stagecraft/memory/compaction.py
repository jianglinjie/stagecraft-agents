"""Compaction: replace the old part of a long history with a summary.

The cut always lands on a user message, so the kept tail starts at a turn boundary
and never separates a tool call from its result. The most recent turns are kept
verbatim; everything before them becomes one assistant message.

A compactor is any async callable from items to summary text. :class:`ModelCompactor`
is the real one; tests pass a plain function.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agents import Agent, Model, RunConfig, Runner

from stagecraft.memory.items import is_user_message, render_transcript

Compactor = Callable[[list[Any]], Awaitable[str]]

SUMMARY_PREFIX = "Summary of the earlier conversation (kept as context, not a new request):\n"

COMPACTOR_INSTRUCTIONS = """\
You compress a conversation between a user and a content-production assistant so work can
continue without the full transcript.

Keep: the user's goals and constraints, every decision and approval, ids that were produced
(plan, stage, brief, outline, draft, render, asset names) and what each is, open questions,
and anything the user rejected. Drop: pleasantries, restated tool output, reasoning that led
nowhere.

Write plain sentences, no headings, under 250 words. Output only the summary."""


def find_cut(items: Sequence[Any], *, keep_recent_user_turns: int) -> int:
    """Index where the verbatim tail begins; 0 means there is nothing to compact."""
    user_indexes = [i for i, item in enumerate(items) if is_user_message(item)]
    if len(user_indexes) <= keep_recent_user_turns:
        return 0
    return user_indexes[len(user_indexes) - keep_recent_user_turns]


class ModelCompactor:
    def __init__(self, model: Model) -> None:
        self.model = model

    async def __call__(self, items: list[Any]) -> str:
        agent = Agent(name="compactor", instructions=COMPACTOR_INSTRUCTIONS, model=self.model)
        result = await Runner.run(
            agent,
            render_transcript(items),
            max_turns=1,
            run_config=RunConfig(tracing_disabled=True),
        )
        summary = str(result.final_output or "").strip()
        if not summary:
            raise ValueError("compactor returned an empty summary")
        return summary
