"""Who is calling a tool.

The role is a property of the caller, set by whoever starts the run — never a
parameter the model fills in. A tool that declares a ``RunContext`` parameter
gets it injected from the SDK run context; the parameter is left out of the
schema, so the model cannot even see it, let alone forge it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["orchestrator", "router", "planner", "executor"]


@dataclass(frozen=True)
class RunContext:
    role: Role
    chat_id: str
    turn_id: str | None = None
    message_id: str | None = None
    extras: dict[str, str] = field(default_factory=dict)

    def as_role(self, role: Role) -> RunContext:
        """The same caller identity, handed down to a sub-agent with a new role."""
        return RunContext(
            role=role,
            chat_id=self.chat_id,
            turn_id=self.turn_id,
            message_id=self.message_id,
            extras=dict(self.extras),
        )
