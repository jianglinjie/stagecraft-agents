"""One agent, tools picked from the registry by name.

The agent does not build tools; it names the ones it wants and the registry hands
back SDK ``FunctionTool`` objects. The model is either injected (a ``FakeModel`` in
tests) or built from the environment: an OpenAI-compatible Chat Completions
endpoint, so any provider with that API works.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from agents import Agent, Model, OpenAIChatCompletionsModel, RunConfig, Runner, RunResult
from agents.items import TResponseInputItem
from openai import AsyncOpenAI

from stagecraft.config import MissingConfigError, ModelConfig
from stagecraft.tools.registry import ToolRegistry

DEFAULT_INSTRUCTIONS = """\
You produce content in stages using the tools you are given.
Work strictly from tool results: pass on the ids they return and never invent one.
When a tool answers with status "error", read its issues and hint, fix the call once,
and if it still fails explain the problem to the user instead of guessing.
When the requested work is done, reply with a short summary that names the final id."""


def build_openai_model(config: ModelConfig | None = None, *, max_retries: int = 2) -> Model:
    """A Chat Completions model against whatever endpoint the environment names."""
    config = config or ModelConfig.from_env()
    client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key, max_retries=max_retries)
    return OpenAIChatCompletionsModel(model=config.model, openai_client=client)


def build_single_agent(
    registry: ToolRegistry,
    tool_names: Sequence[str],
    *,
    model: Model | None = None,
    instructions: str = DEFAULT_INSTRUCTIONS,
    name: str = "single-agent",
    strict_schema: bool = False,
) -> Agent:
    """An agent holding exactly the named tools, in that order."""
    return Agent(
        name=name,
        instructions=instructions,
        tools=registry.select(*tool_names, strict=strict_schema),
        model=model or build_openai_model(),
    )


async def run_single_agent(
    agent: Agent,
    prompt: str | list[TResponseInputItem],
    *,
    max_turns: int = 10,
    tracing: bool = False,
) -> RunResult:
    """Run one user turn. Tracing is off unless asked: it would try to reach OpenAI.

    ``prompt`` is a fresh user message, or the item list from :func:`follow_up` to
    continue a conversation.
    """
    return await Runner.run(
        agent,
        prompt,
        max_turns=max_turns,
        run_config=RunConfig(tracing_disabled=not tracing),
    )


def follow_up(previous: RunResult, text: str) -> list[TResponseInputItem]:
    """The input for the next turn: everything the last run saw and produced, plus ``text``.

    This is the whole of conversation memory until milestone 3 adds persisted
    sessions: the caller carries the item list between runs.
    """
    return [*previous.to_input_list(), {"role": "user", "content": text}]


def format_trace(result: RunResult) -> list[str]:
    """One line per tool call and per tool result, for eyeballing a run."""
    lines: list[str] = []
    for item in result.new_items:
        if item.type == "tool_call_item":
            raw = item.raw_item
            name = getattr(raw, "name", None) or getattr(raw, "type", "tool")
            lines.append(f"-> {name}({getattr(raw, 'arguments', '')})")
        elif item.type == "tool_call_output_item":
            lines.append(f"<- {item.output}")
    return lines


async def _main(argv: Sequence[str]) -> int:
    from stagecraft.tools.fake import build_fake_registry

    args = list(argv)
    chat = "--chat" in args
    if chat:
        args.remove("--chat")
    prompt = " ".join(args) or "Turn https://example.com/products/1 into a short HTML article."

    registry, workspace = build_fake_registry()
    try:
        agent = build_single_agent(registry, registry.names())
    except MissingConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    result = await _turn(agent, prompt, workspace)
    if not chat:
        return 0

    print("chat mode: type a follow-up, or exit / Ctrl-D to quit", file=sys.stderr)
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 0
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            return 0
        result = await _turn(agent, follow_up(result, text), workspace)


async def _turn(
    agent: Agent, prompt: str | list[TResponseInputItem], workspace: object
) -> RunResult:
    result = await run_single_agent(agent, prompt)
    for line in format_trace(result):
        print(line, file=sys.stderr)
    print(f"workspace: {workspace.ids()}", file=sys.stderr)  # type: ignore[attr-defined]
    print(result.final_output)
    return result


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
