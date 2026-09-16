"""Assets seen from agents: one exit for lookups, archive refusals, Turn Context."""

from typing import Any

from stagecraft.agents.fake_model import FakeModel, reply, tool_call
from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.runtime import AgentRuntime, build_runtime
from stagecraft.assets import ArchiveReason, NewAsset
from stagecraft.memory import LongTermMemory
from stagecraft.plan import StageContract
from stagecraft.tools.fake import FakeWorkspace


def make(scripts: dict[str, list[Any]], **kwargs: Any) -> tuple[AgentRuntime, dict[str, FakeModel]]:
    models = {role: FakeModel(scripts.get(role, [])) for role in ROLE_TOOLS}
    runtime = build_runtime(
        chat_id="chat_1",
        models=models,  # type: ignore[arg-type]
        workspace=FakeWorkspace(render_delay_seconds=0),
        **kwargs,
    )
    return runtime, models


def last_output(model: FakeModel, call: int) -> dict[str, Any]:
    return model.calls[call].tool_outputs()[-1]


async def test_every_name_lookup_refuses_an_archived_asset_the_same_way() -> None:
    runtime, models = make(
        {
            "orchestrator": [
                tool_call("fetch_brief", source="s"),
                tool_call("write_outline", brief_id="brief_0001"),
                tool_call(
                    "write_draft", outline_id="outline_0001", reference_assets=["hero", "logo"]
                ),
                tool_call("asset_get", name="hero"),
                tool_call("write_draft", outline_id="outline_0001", reference_assets=["logo"]),
                reply("ok"),
            ]
        }
    )
    assert runtime.assets is not None
    runtime.assets.register("chat_1", NewAsset(name="hero", kind="image", source_id="h"))
    runtime.assets.register("chat_1", NewAsset(name="logo", kind="image", source_id="l"))
    runtime.assets.archive("chat_1", ["hero"], reason=ArchiveReason.PANEL)

    await run_orchestrator_turn(runtime, "draft it")
    model = models["orchestrator"]

    from_draft, from_get = last_output(model, 3), last_output(model, 4)
    assert from_draft["code"] == from_get["code"] == "asset_archived"
    assert from_draft["hint"] == from_get["hint"]
    assert runtime.workspace.ids("draft") == ["draft_0001"]
    assert last_output(model, 5)["reference_assets"] == ["logo"]


async def test_archiving_an_asset_a_stage_uses_is_refused_until_forced() -> None:
    runtime, models = make(
        {
            "orchestrator": [
                tool_call("archive_session_assets", names=["hero"], reason="user_request"),
                tool_call(
                    "archive_session_assets", names=["hero"], reason="user_request", force=True
                ),
                reply("archived"),
            ]
        }
    )
    assert runtime.assets is not None
    runtime.assets.register("chat_1", NewAsset(name="hero", kind="image", source_id="h"))
    plan = runtime.store.create_plan(chat_id="chat_1", objective="o", role="orchestrator")
    runtime.store.write_stage_contract(
        plan_id=plan.id, role="planner", contract=StageContract(goal="banner", assets=["hero"])
    )

    await run_orchestrator_turn(runtime, "stop using hero", message_id="m42")
    model = models["orchestrator"]

    refused = last_output(model, 1)
    assert refused["code"] == "assets_in_use"
    assert refused["issues"] == ["hero: used by stage_01"]

    forced = last_output(model, 2)
    assert forced["archived"] == ["hero"]
    assert forced["already_archived"] == [], "the refused call must not have archived anything"
    assert forced["dependent_stages"] == {"hero": ["stage_01"]}
    (archived,) = runtime.assets.archived("chat_1")
    assert archived.archive is not None
    assert (archived.archive.reason, archived.archive.message_id) == ("user_request", "m42")


async def test_turn_context_is_in_every_model_call_and_never_in_memory() -> None:
    runtime, models = make(
        {"orchestrator": [tool_call("plan_create", objective="series"), reply("created")]},
        long_term=None,
    )
    assert runtime.assets is not None
    hero = runtime.assets.register("chat_1", NewAsset(name="hero", kind="image", source_id="h"))
    plan = runtime.store.create_plan(chat_id="chat_1", objective="old", role="orchestrator")
    runtime.store.write_stage_contract(
        plan_id=plan.id, role="planner", contract=StageContract(goal="banner", assets=["hero"])
    )
    changes = runtime.assets.apply_turn_changes(
        "chat_1",
        message_id="m1",
        register=[NewAsset(name="notes", kind="document", source_id="n")],
        archive=["hero"],
    )
    assert hero.created

    await run_orchestrator_turn(runtime, "make a series", remember=True, changes=changes)
    first, second = models["orchestrator"].calls

    assert "## Turn Context" in (first.system_instructions or "")
    assert "- notes (document)" in (first.system_instructions or "")
    assert "hero: still used by stage_01" in (first.system_instructions or "")
    assert "plan_0001 (rev 1)" in (first.system_instructions or "")
    assert "plan_0002" in (second.system_instructions or ""), "rebuilt after plan_create"

    stored = await runtime.memory("chat:chat_1", "orchestrator").get_items()
    assert stored and "Turn Context" not in str(stored)


async def test_the_topic_profile_reaches_the_turn_context() -> None:
    from stagecraft.db import Database

    long_term = LongTermMemory(Database())

    async def rewrite(_: str, __: str) -> str:
        return "- readers are developers"

    await long_term.extract("articles", [{"role": "user", "content": "hi"}], rewrite)
    runtime, models = make({"orchestrator": [reply("ok")]}, long_term=long_term, topic="articles")
    await run_orchestrator_turn(runtime, "go")

    instructions = models["orchestrator"].calls[0].system_instructions or ""
    assert "What earlier sessions learned about articles" in instructions
    assert "- readers are developers" in instructions
