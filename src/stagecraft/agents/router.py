"""Router: decides direct or workflow. Holds no tools, does no work."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents import Agent
from pydantic import BaseModel, ConfigDict

from stagecraft.agents.submit import stop_on_submit
from stagecraft.tools.results import ToolResult

if TYPE_CHECKING:
    from stagecraft.agents.runtime import AgentRuntime

ROUTER_INSTRUCTIONS = """\
You are the Router of a content pipeline. You decide how a request is handled; you do no work.

Your input is a JSON payload with the user's request.

Return route "direct" when one piece can be produced in a single pass with no review in between:
one brief, one outline, one draft, one render.

Return route "workflow" when the request needs several deliverables that depend on each other,
needs the user to approve an intermediate result before continuing, or is too large for one
pass (a series, several articles, several formats built from different drafts).

The reason is one sentence naming the deciding factor.
Answer by calling submit_route. Do not reply with text."""


class RouterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str


class RoutingCapsule(ToolResult):
    route: Literal["direct", "workflow"]
    reason: str


SUBMIT_TOOL = "submit_route"


def build_router(runtime: AgentRuntime) -> Agent:
    return Agent(
        name="router",
        instructions=runtime.instructions_for("router", ROUTER_INSTRUCTIONS),
        model=runtime.model("router"),
        tools=runtime.tools("router"),
        tool_use_behavior=stop_on_submit(SUBMIT_TOOL),
    )
