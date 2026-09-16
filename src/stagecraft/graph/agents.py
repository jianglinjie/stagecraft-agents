"""The model calls inside the LangGraph version.

The graph owns control flow and state, so its agents are narrower than the Agents SDK
version's: none of them reads or writes the Plan Store through tools. A node hands an
agent exactly the slice of state it needs and applies what the agent submits.

They still submit through tools (see ``agents/submit.py``), and the router is the same
router as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agents import Agent, Model, RunConfig, Runner, RunResult
from pydantic import BaseModel, ConfigDict, Field

from stagecraft.agents.router import ROUTER_INSTRUCTIONS, RoutingCapsule
from stagecraft.agents.submit import build_submit_tool, read_submission, stop_on_submit
from stagecraft.plan import RuntimeRef, WorkItem
from stagecraft.tools.fake import FakeWorkspace, build_fake_tools
from stagecraft.tools.registry import ToolRegistry
from stagecraft.tools.results import ToolResult

CONTENT_TOOLS = ("fetch_brief", "write_outline", "write_draft", "render_output")


class StageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    work_items: list[WorkItem]
    inputs: list[int] = Field(
        default_factory=list, description="Orders (1-based) of earlier stages this one builds on."
    )
    acceptance: str = ""


class GraphPlannerOutput(ToolResult):
    summary: str
    questions: list[str]
    stages: list[StageDraft]


class GraphExecutorOutput(ToolResult):
    summary: str
    refs: list[RuntimeRef]
    failed_item_ids: list[str]


GRAPH_PLANNER_INSTRUCTIONS = """\
You are the Planner. You design stages; you never execute them.

Your input is JSON: goal, answers (the user's replies to your earlier questions), and optionally
revise (one stage to rewrite) with feedback.
- New plan: submit two to four stages in dependency order. inputs lists the orders of earlier
  stages a stage builds on. Every work item must map to one Executor capability below.
- Revision: submit exactly one stage, the rewritten one, applying the feedback.
- If a decision only the user can make is missing, submit no stages and ask it in questions.
Finish by calling submit_graph_plan.

Executor capabilities: fetch_brief, write_outline, write_draft, render_output."""

GRAPH_EXECUTOR_INSTRUCTIONS = """\
You are the Executor. You execute one stage.

Your input is JSON: goal, work_items, acceptance, upstream_refs (ids from earlier stages to build
on) and retry_ids (when present, only those work items). Call the content tools, passing ids,
never content. Then call submit_graph_execution with one ref per finished work item (ref_id from
the tool result, kind brief/outline/draft/render, work_item_id, summary) and the ids of items
you could not finish."""

DIRECT_INSTRUCTIONS = """\
You produce one piece of content with the tools, in a single pass, passing ids between tools.
Reply with a short summary naming the final id."""


@dataclass
class GraphDeps:
    models: Mapping[str, Model]
    workspace: FakeWorkspace = field(default_factory=FakeWorkspace)
    max_attempts: int = 2
    max_turns: int = 20
    registry: ToolRegistry = field(init=False)

    def __post_init__(self) -> None:
        self.registry = ToolRegistry(
            [
                *build_fake_tools(self.workspace),
                build_submit_tool(
                    RoutingCapsule, name="submit_route", description="Submit the route."
                ),
                build_submit_tool(
                    GraphPlannerOutput,
                    name="submit_graph_plan",
                    description="Submit stages or questions. This ends your run.",
                ),
                build_submit_tool(
                    GraphExecutorOutput,
                    name="submit_graph_execution",
                    description="Submit the refs you produced. This ends your run.",
                ),
            ]
        )

    def agent(self, role: str) -> Agent:
        match role:
            case "router":
                return self._agent(role, ROUTER_INSTRUCTIONS, ("submit_route",), "submit_route")
            case "planner":
                return self._agent(
                    role, GRAPH_PLANNER_INSTRUCTIONS, ("submit_graph_plan",), "submit_graph_plan"
                )
            case "executor":
                return self._agent(
                    role,
                    GRAPH_EXECUTOR_INSTRUCTIONS,
                    (*CONTENT_TOOLS, "submit_graph_execution"),
                    "submit_graph_execution",
                )
            case "direct":
                return Agent(
                    name="direct",
                    instructions=DIRECT_INSTRUCTIONS,
                    model=self.models["direct"],
                    tools=self.registry.select(*CONTENT_TOOLS),
                )
        raise KeyError(role)

    async def run(self, role: str, payload: BaseModel | dict[str, Any] | str) -> RunResult:
        if isinstance(payload, BaseModel):
            text = payload.model_dump_json()
        elif isinstance(payload, dict):
            import json

            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = payload
        return await Runner.run(
            self.agent(role),
            text,
            max_turns=self.max_turns,
            run_config=RunConfig(tracing_disabled=True),
        )

    async def submit[T: ToolResult](self, role: str, payload: Any, output: type[T]) -> T:
        return read_submission(await self.run(role, payload), output, role=role)

    def _agent(self, role: str, instructions: str, tools: tuple[str, ...], submit: str) -> Agent:
        return Agent(
            name=f"graph-{role}",
            instructions=instructions,
            model=self.models[role],
            tools=self.registry.select(*tools),
            tool_use_behavior=stop_on_submit(submit),
        )
