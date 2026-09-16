"""Planner: authors stage contracts into the Plan Store. Never executes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import Agent
from pydantic import BaseModel, ConfigDict, Field

from stagecraft.agents.submit import stop_on_submit
from stagecraft.tools.results import ToolResult

if TYPE_CHECKING:
    from stagecraft.agents.runtime import AgentRuntime

PLANNER_INSTRUCTIONS = """\
You are the Planner. You author stages into the Plan Store; you never execute them.

Your input is a JSON payload: plan_id, goal, and optionally stage_id (rewrite that one stage)
and answers (the user's replies to questions you asked before).

- Write one stage per plan_write_stage_contract call, in dependency order.
- Each stage needs a goal, concrete work_items with ids unique inside the stage, inputs listing
  the upstream stage ids it builds on, and a one-line acceptance test.
- Keep plans small: two to four stages.
- Every work item must be something the Executor can do with exactly one of its capabilities
  below. Do not plan work no capability covers.
- When a stage depends on an audience, tone, format or series convention, call
  search_references first and put the pointers you rely on in that stage's sources. Copy
  pointers exactly as returned; never make one up. No relevant hit means no sources.
- If a decision only the user can make is missing (audience, tone, output format), do not guess.
  Write no stages and put the questions in your output.
- Finish by calling submit_plan: a one-line summary, the questions (empty if none), and whether
  authoring is complete. Do not end with text."""


class PlannerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    stage_id: str | None = None
    answers: list[str] = Field(default_factory=list)


class PlannerOutput(ToolResult):
    summary: str
    questions: list[str]
    done_authoring: bool


SUBMIT_TOOL = "submit_plan"


def build_planner(runtime: AgentRuntime) -> Agent:
    return Agent(
        name="planner",
        instructions=(
            runtime.instructions_for("planner", PLANNER_INSTRUCTIONS)
            + "\n\n"
            + executor_capabilities(runtime)
        ),
        model=runtime.model("planner"),
        tools=runtime.tools("planner"),
        tool_use_behavior=stop_on_submit(SUBMIT_TOOL),
    )


def executor_capabilities(runtime: AgentRuntime) -> str:
    """What the Executor can actually do, read from the registry so it never drifts."""
    from stagecraft.agents.roles import CONTENT_TOOLS

    lines = [f"- {name}: {runtime.registry.get(name).description}" for name in CONTENT_TOOLS]
    return "Executor capabilities:\n" + "\n".join(lines)
