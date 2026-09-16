"""Everything one chat's agents share: store, workspace, registry, models.

``build_runtime`` is the single place tools are assembled. Content tools and plan
tools are registered first; dispatch tools last, because they need the runtime
itself to start sub-agent runs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agents import Agent, FunctionTool, Model, RunConfig, Runner, RunResult
from pydantic import BaseModel

from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.plan import PlanStore
from stagecraft.tools.context import Role, RunContext
from stagecraft.tools.fake import FakeWorkspace, build_fake_tools
from stagecraft.tools.plan import build_plan_tools
from stagecraft.tools.registry import ToolRegistry

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
    extras: dict[str, Any] = field(default_factory=dict)

    def model(self, role: Role) -> Model:
        try:
            return self.models[role]
        except KeyError:
            raise KeyError(f"no model configured for role {role!r}") from None

    def tools(self, role: Role) -> list[FunctionTool]:
        return self.registry.select(*ROLE_TOOLS[role])

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
) -> AgentRuntime:
    from stagecraft.agents.dispatch import build_dispatch_tools, build_submit_tools

    store = PlanStore() if store is None else store
    workspace = FakeWorkspace() if workspace is None else workspace
    registry = ToolRegistry([*build_fake_tools(workspace), *build_plan_tools(store)])
    runtime = AgentRuntime(
        chat_id=chat_id,
        store=store,
        workspace=workspace,
        registry=registry,
        models=models,
        max_turns=max_turns,
    )
    for spec in [*build_submit_tools(), *build_dispatch_tools(runtime)]:
        registry.register(spec)
    return runtime


def same_model_for_all_roles(model: Model) -> dict[Role, Model]:
    return {role: model for role in ROLE_TOOLS}
