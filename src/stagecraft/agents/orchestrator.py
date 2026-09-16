"""Orchestrator: the only agent the user talks to.

It routes, creates plans, moves stages through the state machine and dispatches
sub-agents. It never authors a contract and never attaches runtime refs: the
store would refuse it, because those halves belong to other roles.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from agents import Agent, Runner, RunResult
from agents.items import TResponseInputItem
from pydantic import BaseModel

from stagecraft.agents.runtime import AgentRuntime, build_runtime, same_model_for_all_roles
from stagecraft.agents.single import build_openai_model, follow_up, format_trace
from stagecraft.config import MissingConfigError
from stagecraft.tools.context import Role

ORCHESTRATOR_INSTRUCTIONS = """\
You are the Orchestrator of a staged content pipeline (brief -> outline -> draft -> render).
You talk to the user. Sub-agents do focused work and return to you.

For a new request, call dispatch_router first and follow its route.

Direct route: use the content tools yourself, then reply naming the final id.

Workflow route:
1. plan_create with the objective.
2. dispatch_planner with plan_id and goal. If it returns questions, ask the user and end the turn.
3. For the next pending stage request waiting_user with review_kind=plan_review. Present the
   stages (order, goal, work items) and end the turn. Never approve on the user's behalf.
4. After the user explicitly approves: request doing with user_confirmed=true, then
   dispatch_executor with plan_id, stage_id, order and goal only.
5. If pending_items is empty, request done. Otherwise retry once with retry_ids, then request
   blocked with a reason.
6. Continue with the next pending stage from step 3.

Rules:
- The Plan Store is the truth. Follow next_action from tool results; never infer progress from
  the conversation.
- Never invent ids. Never copy contracts, briefs or history into a dispatch payload.
- On an error result, read its code and hint before doing anything else."""


def build_orchestrator(runtime: AgentRuntime) -> Agent:
    return Agent(
        name="orchestrator",
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        model=runtime.model("orchestrator"),
        tools=runtime.tools("orchestrator"),
    )


async def run_orchestrator_turn(
    runtime: AgentRuntime,
    user_input: str | list[TResponseInputItem],
    **context_kwargs: str,
) -> RunResult:
    return await Runner.run(
        build_orchestrator(runtime),
        user_input,
        context=runtime.context("orchestrator", **context_kwargs),
        max_turns=runtime.max_turns,
        run_config=runtime.run_config(),
    )


def _print_sub_run(role: Role, payload: BaseModel, result: RunResult) -> None:
    print(f"   [{role}] payload {payload.model_dump_json()}", file=sys.stderr)
    for line in format_trace(result):
        print(f"   [{role}] {line}", file=sys.stderr)


async def _main(argv: Sequence[str]) -> int:
    prompt = " ".join(argv) or "Write a two-part series about https://example.com/p/1."
    try:
        model = build_openai_model()
    except MissingConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    runtime = build_runtime(chat_id="cli", models=same_model_for_all_roles(model))
    runtime.on_sub_run = _print_sub_run

    user_input: str | list[TResponseInputItem] = prompt
    while True:
        result = await run_orchestrator_turn(runtime, user_input)
        for line in format_trace(result):
            print(line, file=sys.stderr)
        plan = runtime.store.latest_for_chat("cli")
        if plan is not None:
            print(plan.summary(), file=sys.stderr)
        print(result.final_output)
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 0
        if not text or text.lower() in {"exit", "quit"}:
            return 0
        user_input = follow_up(result, text)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
