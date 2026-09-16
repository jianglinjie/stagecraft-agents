"""Executor: runs exactly one stage from a pointer, records refs, returns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import Agent
from pydantic import BaseModel, ConfigDict, Field

from stagecraft.agents.submit import stop_on_submit
from stagecraft.tools.results import ToolResult

if TYPE_CHECKING:
    from stagecraft.agents.runtime import AgentRuntime

EXECUTOR_INSTRUCTIONS = """\
You are the Executor. You execute exactly one stage and record what it produced.

Your input is a JSON payload: plan_id, stage_id, order, goal, and optionally retry_ids.

1. Call plan_get_stage_detail. The contract is your job; upstream_refs are the ids you build on.
   Do nothing the contract does not ask for.
2. For each work item (only those in retry_ids, when given) call the content tools, passing ids
   from upstream_refs or from your own earlier results in this run. Pass ids, never content.
3. Call plan_attach_runtime once with one ref per finished work item: ref_id is the id the
   content tool returned, kind is brief, outline, draft or render, work_item_id is the item id,
   summary is the tool's summary.
4. Finish by calling submit_execution with a summary and the ids of work items you could not
   finish. Do not end with text."""


class ExecutorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    stage_id: str
    order: int
    goal: str
    retry_ids: list[str] = Field(default_factory=list)


class ExecutorOutput(ToolResult):
    summary: str
    failed_item_ids: list[str]


SUBMIT_TOOL = "submit_execution"


def build_executor(runtime: AgentRuntime) -> Agent:
    return Agent(
        name="executor",
        instructions=EXECUTOR_INSTRUCTIONS,
        model=runtime.model("executor"),
        tools=runtime.tools("executor"),
        tool_use_behavior=stop_on_submit(SUBMIT_TOOL),
    )
