"""Helpers over stored conversation items: size estimates and history repair.

Stored history is written by one version of the agent and read by another. Two
things go wrong in practice, and both would otherwise break the next turn:

* **a tool was renamed or removed.** The history holds structured calls to a tool
  the agent no longer has. Models tend to try calling it again.
* **a call never got its output.** The process was killed mid-tool. Chat Completions
  rejects an assistant tool call that is not followed by its tool result, so the
  next request fails outright.

:func:`repair_history` turns both into short assistant notes that keep the
information, and drops tool outputs whose call is missing. It is a *view*: stored
rows are untouched, so a tool that comes back under its old name reads fine again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

Item = dict[str, Any]

NOTE_LIMIT = 400


def estimate_tokens(items: Iterable[Any]) -> int:
    """About four characters per token over the JSON form.

    Crude and provider-independent on purpose: it only decides *when* to compact,
    and being early by a few percent costs nothing.
    """
    return sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in items) // 4


def is_user_message(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("role") == "user"
        and item.get("type", "message") == "message"
    )


@dataclass
class RepairReport:
    retired_tools: set[str] = field(default_factory=set)
    interrupted_calls: list[str] = field(default_factory=list)
    orphan_outputs: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.retired_tools or self.interrupted_calls or self.orphan_outputs)


def repair_history(
    items: Sequence[Any], known_tools: set[str] | None
) -> tuple[list[Any], RepairReport]:
    report = RepairReport()
    calls = {
        item["call_id"]: item
        for item in items
        if isinstance(item, dict) and item.get("type") == "function_call"
    }
    outputs = {
        item["call_id"]: item
        for item in items
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }
    folded: set[str] = set()
    repaired: list[Any] = []

    for item in items:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "function_call":
            call_id, name = item["call_id"], item.get("name", "tool")
            output = outputs.get(call_id)
            if output is None:
                report.interrupted_calls.append(call_id)
                folded.add(call_id)
                repaired.append(
                    _note(
                        f"[A call to {name} was interrupted before it returned; its effect is "
                        "unknown. Check the current state with a tool before relying on it.]"
                    )
                )
            elif known_tools is not None and name not in known_tools:
                report.retired_tools.add(name)
                folded.add(call_id)
                repaired.append(
                    _note(
                        f"[Earlier call to {name}, a tool that no longer exists. "
                        f"Arguments: {_clip(item.get('arguments'))}. "
                        f"Result: {_clip(output.get('output'))}]"
                    )
                )
            else:
                repaired.append(item)
        elif kind == "function_call_output":
            call_id = item["call_id"]
            if call_id not in calls:
                report.orphan_outputs.append(call_id)
            elif call_id not in folded:
                repaired.append(item)
        else:
            repaired.append(item)
    return repaired, report


def render_transcript(items: Sequence[Any], *, limit: int = 1500) -> str:
    """A plain-text transcript for a summariser or a memory rewriter."""
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "function_call":
            lines.append(f"tool call {item.get('name')}({_clip(item.get('arguments'), limit)})")
        elif kind == "function_call_output":
            lines.append(f"tool result: {_clip(item.get('output'), limit)}")
        elif "role" in item:
            lines.append(f"{item['role']}: {_clip(_content_text(item.get('content')), limit)}")
    return "\n".join(lines)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        ).strip()
    return str(content)


def _note(text: str) -> Item:
    return {"role": "assistant", "content": text}


def _clip(value: Any, limit: int = NOTE_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"
