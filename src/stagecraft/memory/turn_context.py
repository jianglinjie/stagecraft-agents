"""Turn Context: rebuilt for every model call, never stored.

Three layers of memory, by lifetime:

1. **Turn Context** (this module). Assembled from the stores each time the model is
   called and appended to the system instructions: the plan's current summary, the
   active assets as pointers, what this message uploaded or archived and which
   stages that affects, and the topic's long-term profile. Because it lives in the
   instructions, the SDK never writes it into the session, so it cannot go stale in
   history and it costs nothing on the next turn.
2. **Session memory** (``session_memory.py``). The conversation itself, written back at
   the end of every run, compacted when it grows.
3. **Long-term memory** (``long_term.py``). One profile per topic, rewritten after a
   session ends.

Only pointers go in: names, ids, one-line summaries. An agent that needs the content
calls a tool for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stagecraft.assets import Asset, AssetStore, TurnAssetChanges
from stagecraft.memory.long_term import LongTermMemory, MemoryProfile
from stagecraft.plan import Plan, PlanStore


@dataclass
class TurnContext:
    plan: Plan | None = None
    active_assets: list[Asset] = field(default_factory=list)
    registered_this_turn: list[Asset] = field(default_factory=list)
    archived_this_turn: list[Asset] = field(default_factory=list)
    dependent_stages: dict[str, list[str]] = field(default_factory=dict)
    profile: MemoryProfile | None = None

    def render(self) -> str:
        lines = [
            "## Turn Context",
            "Current state for this turn. Pointers only; use tools for content.",
        ]

        lines.append("\n### Plan")
        lines.append(self.plan.summary() if self.plan else "No plan yet.")

        lines.append("\n### Active assets")
        if self.active_assets:
            lines += [
                f"- {a.name} ({a.kind}): {a.summary or 'no summary'}" for a in self.active_assets
            ]
        else:
            lines.append("None.")

        if self.registered_this_turn:
            lines.append("\n### Added with this message")
            lines += [f"- {a.name} ({a.kind})" for a in self.registered_this_turn]

        if self.archived_this_turn:
            lines.append("\n### Archived by the user with this message")
            for asset in self.archived_this_turn:
                stages = self.dependent_stages.get(asset.name)
                if stages:
                    lines.append(
                        f"- {asset.name}: still used by {', '.join(stages)}. Say so and agree on a "
                        "replacement before continuing those stages."
                    )
                else:
                    lines.append(f"- {asset.name}: no unfinished stage uses it.")

        if self.profile and self.profile.content:
            lines.append(f"\n### What earlier sessions learned about {self.profile.topic}")
            lines.append(self.profile.content)

        return "\n".join(lines)


def build_turn_context(
    *,
    chat_id: str,
    plans: PlanStore,
    assets: AssetStore | None,
    changes: TurnAssetChanges | None = None,
    long_term: LongTermMemory | None = None,
    topic: str | None = None,
) -> TurnContext:
    return TurnContext(
        plan=plans.latest_for_chat(chat_id),
        active_assets=assets.active(chat_id) if assets else [],
        registered_this_turn=[r.asset for r in changes.registered] if changes else [],
        archived_this_turn=list(changes.archive.archived) if changes else [],
        dependent_stages=dict(changes.archive.dependent_stages) if changes else {},
        profile=long_term.get(topic) if long_term and topic else None,
    )
