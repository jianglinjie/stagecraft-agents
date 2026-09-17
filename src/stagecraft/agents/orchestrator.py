"""Orchestrator: the only agent the user talks to.

It routes, creates plans, moves stages through the state machine and dispatches
sub-agents. It never authors a contract and never attaches runtime refs: the
store would refuse it, because those halves belong to other roles.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from agents import (
    Agent,
    FunctionToolResult,
    RunContextWrapper,
    Runner,
    RunResult,
    RunResultStreaming,
    ToolsToFinalOutputResult,
)
from agents.items import TResponseInputItem
from pydantic import BaseModel

from stagecraft.agents.runtime import AgentRuntime, build_runtime, same_model_for_all_roles
from stagecraft.agents.single import build_openai_model, format_trace
from stagecraft.assets import TurnAssetChanges
from stagecraft.config import MissingConfigError
from stagecraft.tools.context import Role

ORCHESTRATOR_INSTRUCTIONS = """\
You are the Orchestrator of a staged content pipeline (brief -> outline -> draft -> render).
You talk to the user. Sub-agents do focused work and return to you.

For a new request, call dispatch_router first and follow its route. The exception: if the
request names nothing the tools can work from (no URL, and no product or topic described
specifically enough to fetch a brief for), ask the user for it and end the turn. Do not fill
the gap with a guess.

Direct route: use the content tools yourself, then reply naming the final id.

Workflow route:
1. plan_create with the objective.
2. dispatch_planner with plan_id and a goal that says what to produce: the deliverables and the
   audience, tone and format the user gave. Leave out how or when the user wants to review; the
   Planner cannot run a review, you do. If the Planner has questions, the turn ends by itself;
   when the user answers, dispatch_planner again with the same plan_id and their answers.
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
- A dispatch changes the plan. On the first write after dispatch_planner or dispatch_executor,
  omit expected_revision rather than reuse one you read before the dispatch.
- Never invent ids. Never copy contracts, briefs or history into a dispatch payload.
- On an error result, read its code and hint before doing anything else.
- When you cannot do what the user asked, say why in one sentence and ask which of the possible
  next steps they want.
- Assets: refer to them by name. Archive only when the user explicitly asks in chat, or when an
  output you just made replaces an earlier version. Archiving is final. If archive_session_assets
  reports stages still using an asset, name those stages to the user and replan or get their
  confirmation before calling again with force=true. When the Turn Context says the user archived
  an asset that a stage relies on, say so and agree on a replacement before continuing."""


def stop_on_interrupt(
    _ctx: RunContextWrapper[Any], results: list[FunctionToolResult]
) -> ToolsToFinalOutputResult:
    """End the turn in code the moment a tool reports that the user must answer first.

    Asking the model to "stop and wait" is a request; this is a guarantee. The model is
    not called again this turn, so it cannot keep dispatching past the question.
    """
    for result in results:
        data = _json_object(result.output)
        if data and data.get("interrupt"):
            questions = [str(q) for q in data.get("questions", [])]
            lines = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=f"Before planning can continue, please answer:\n{lines}",
            )
    return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


def build_orchestrator(runtime: AgentRuntime, changes: TurnAssetChanges | None = None) -> Agent:
    def instructions(_ctx: RunContextWrapper[Any], _agent: Agent) -> str:
        # Rebuilt on every model call, so the plan summary is current mid-turn, and never
        # written to session memory, because the SDK does not store instructions.
        base = runtime.instructions_for("orchestrator", ORCHESTRATOR_INSTRUCTIONS)
        return base + "\n\n" + runtime.turn_context(changes).render()

    return Agent(
        name="orchestrator",
        instructions=instructions,
        model=runtime.model("orchestrator"),
        tools=runtime.tools("orchestrator"),
        tool_use_behavior=stop_on_interrupt,
    )


def chat_session_key(chat_id: str) -> str:
    return f"chat:{chat_id}"


async def run_orchestrator_turn(
    runtime: AgentRuntime,
    user_input: str | list[TResponseInputItem],
    *,
    remember: bool = False,
    changes: TurnAssetChanges | None = None,
    **context_kwargs: str,
) -> RunResult:
    """One user turn. With ``remember`` the chat's session memory supplies and keeps history."""
    memory = None
    if remember:
        memory = runtime.memory(chat_session_key(runtime.chat_id), "orchestrator")
        await memory.maybe_compact(runtime.compactor)
    return await Runner.run(
        build_orchestrator(runtime, changes),
        user_input,
        context=runtime.context("orchestrator", **context_kwargs),
        max_turns=runtime.max_turns,
        run_config=runtime.run_config(),
        session=memory,
    )


async def stream_orchestrator_turn(
    runtime: AgentRuntime,
    user_input: str,
    *,
    changes: TurnAssetChanges | None = None,
    **context_kwargs: str,
) -> RunResultStreaming:
    """The streamed form the HTTP layer uses. History always comes from session memory."""
    memory = runtime.memory(chat_session_key(runtime.chat_id), "orchestrator")
    await memory.maybe_compact(runtime.compactor)
    return Runner.run_streamed(
        build_orchestrator(runtime, changes),
        user_input,
        context=runtime.context("orchestrator", **context_kwargs),
        max_turns=runtime.max_turns,
        run_config=runtime.run_config(),
        session=memory,
    )


def _json_object(output: Any) -> dict[str, Any] | None:
    if isinstance(output, BaseModel):
        return output.model_dump()
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


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
    from stagecraft.memory import ModelCompactor
    from stagecraft.tools.retrieval import reference_index_from_env

    references = reference_index_from_env()
    stats = await references.load()
    print(f"references: {stats.chunks} sections, {stats.mode}", file=sys.stderr)
    runtime = build_runtime(
        chat_id="cli",
        models=same_model_for_all_roles(model),
        compactor=ModelCompactor(model),
        references=references,
    )
    runtime.on_sub_run = _print_sub_run

    user_input = prompt
    while True:
        result = await run_orchestrator_turn(runtime, user_input, remember=True)
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
        user_input = text


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
