"""Everything one chat's agents share: store, workspace, registry, models.

``build_runtime`` is the single place tools are assembled. Content tools and plan
tools are registered first; dispatch tools last, because they need the runtime
itself to start sub-agent runs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Agent, FunctionTool, Model, RunConfig, Runner, RunResult
from pydantic import BaseModel

from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.assets import AssetStore, TurnAssetChanges
from stagecraft.db import Database
from stagecraft.memory import (
    Compactor,
    LongTermMemory,
    SessionMemory,
    TurnContext,
    build_turn_context,
)
from stagecraft.plan import PlanStore
from stagecraft.tools.assets import build_asset_tools
from stagecraft.tools.context import Role, RunContext
from stagecraft.tools.fake import FakeWorkspace, build_fake_tools
from stagecraft.tools.plan import build_plan_tools
from stagecraft.tools.registry import ToolRegistry, ToolSpec

SubRunHook = Callable[[Role, BaseModel, RunResult], None]


@dataclass
class AgentRuntime:
    chat_id: str
    store: PlanStore
    workspace: FakeWorkspace
    registry: ToolRegistry
    models: Mapping[Role, Model]
    max_turns: int = 20
    tracing: bool = False
    on_sub_run: SubRunHook | None = None
    assets: AssetStore | None = None
    memory_db: Database = field(default_factory=Database)
    compactor: Compactor | None = None
    long_term: LongTermMemory | None = None
    topic: str | None = None
    memory_threshold_tokens: int = 8000
    extra_role_tools: dict[Role, tuple[str, ...]] = field(default_factory=dict)
    instructions: dict[Role, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    _memories: dict[str, SessionMemory] = field(default_factory=dict, repr=False)

    def memory(self, key: str, role: Role) -> SessionMemory:
        """Session memory for ``key``: the chat for the orchestrator, a task for the planner.

        Reads are repaired against ``role``'s current tool names, so history written by an
        older version of the agent cannot break the next run.
        """
        if key not in self._memories:
            self._memories[key] = SessionMemory(
                key,
                self.memory_db,
                known_tools=self.tool_names(role),
                threshold_tokens=self.memory_threshold_tokens,
            )
        return self._memories[key]

    def turn_context(self, changes: TurnAssetChanges | None = None) -> TurnContext:
        return build_turn_context(
            chat_id=self.chat_id,
            plans=self.store,
            assets=self.assets,
            changes=changes,
            long_term=self.long_term,
            topic=self.topic,
        )

    def instructions_for(self, role: Role, default: str) -> str:
        """``role``'s base prompt: the override an eval run passed in, else ``default``."""
        return self.instructions.get(role, default)

    def model(self, role: Role) -> Model:
        try:
            return self.models[role]
        except KeyError:
            raise KeyError(f"no model configured for role {role!r}") from None

    def tool_names(self, role: Role) -> tuple[str, ...]:
        return (*ROLE_TOOLS[role], *self.extra_role_tools.get(role, ()))

    def tools(self, role: Role) -> list[FunctionTool]:
        return self.registry.select(*self.tool_names(role))

    def context(self, role: Role, **kwargs: Any) -> RunContext:
        return RunContext(role=role, chat_id=self.chat_id, **kwargs)

    def run_config(self) -> RunConfig:
        return RunConfig(tracing_disabled=not self.tracing)

    async def run_sub_agent(
        self,
        agent: Agent,
        payload: BaseModel,
        context: RunContext,
        **run_kwargs: Any,
    ) -> RunResult:
        """Start a fresh run whose entire input is ``payload``.

        No parent history crosses this line. That is the point of dispatch-and-return:
        the sub-agent sees a job description, not the conversation that produced it.
        """
        result = await Runner.run(
            agent,
            payload.model_dump_json(),
            context=context,
            max_turns=self.max_turns,
            run_config=self.run_config(),
            **run_kwargs,
        )
        if self.on_sub_run is not None:
            self.on_sub_run(context.role, payload, result)
        return result


def build_runtime(
    *,
    chat_id: str,
    models: Mapping[Role, Model],
    store: PlanStore | None = None,
    workspace: FakeWorkspace | None = None,
    max_turns: int = 20,
    session_db: Path | None = None,
    memory_db: Database | None = None,
    assets: AssetStore | None = None,
    compactor: Compactor | None = None,
    long_term: LongTermMemory | None = None,
    topic: str | None = None,
    memory_threshold_tokens: int = 8000,
    extra_tools: Sequence[ToolSpec] = (),
    extra_role_tools: Mapping[Role, Sequence[str]] | None = None,
    instructions: Mapping[Role, str] | None = None,
) -> AgentRuntime:
    """Assemble one chat's runtime.

    ``extra_tools`` are registered like any other tool (for example an MCP server's admitted
    tools); ``extra_role_tools`` decides which roles may pick them. ``instructions`` replaces a
    role's base prompt, so an eval can compare prompts without editing code.
    """
    from stagecraft.agents.dispatch import build_dispatch_tools, build_submit_tools

    store = PlanStore() if store is None else store
    workspace = FakeWorkspace() if workspace is None else workspace
    assets = AssetStore(plans=store) if assets is None else assets
    if memory_db is None:
        memory_db = Database(session_db if session_db is not None else ":memory:")
    registry = ToolRegistry(
        [
            *build_fake_tools(workspace, assets),
            *build_plan_tools(store),
            *build_asset_tools(assets),
            *extra_tools,
        ]
    )
    runtime = AgentRuntime(
        chat_id=chat_id,
        store=store,
        workspace=workspace,
        registry=registry,
        models=models,
        max_turns=max_turns,
        assets=assets,
        memory_db=memory_db,
        compactor=compactor,
        long_term=long_term,
        topic=topic,
        memory_threshold_tokens=memory_threshold_tokens,
        extra_role_tools={role: tuple(names) for role, names in (extra_role_tools or {}).items()},
        instructions=dict(instructions or {}),
    )
    for spec in [*build_submit_tools(), *build_dispatch_tools(runtime)]:
        registry.register(spec)
    return runtime


def same_model_for_all_roles(model: Model) -> dict[Role, Model]:
    return {role: model for role in ROLE_TOOLS}
