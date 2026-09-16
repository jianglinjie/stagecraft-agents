from pathlib import Path

from agents import Agent, Runner

from stagecraft.agents.fake_model import FakeModel, reply
from stagecraft.db import Database
from stagecraft.memory import SessionMemory, find_cut
from stagecraft.memory.compaction import SUMMARY_PREFIX


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def call(call_id: str, name: str = "fetch_brief") -> dict:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"}


def output(call_id: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": '{"status":"ok"}'}


LONG = "detail " * 200


async def test_history_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    first = SessionMemory("chat:1", Database(path))
    await first.add_items([user("hello"), assistant("hi")])
    await first.add_items([user("again")])

    reopened = SessionMemory("chat:1", Database(path))
    assert await reopened.get_items() == [user("hello"), assistant("hi"), user("again")]
    assert await reopened.get_items(limit=1) == [user("again")]
    assert await SessionMemory("chat:2", Database(path)).get_items() == []


async def test_pop_and_clear() -> None:
    memory = SessionMemory("s", Database())
    await memory.add_items([user("a"), user("b")])
    assert await memory.pop_item() == user("b")
    await memory.clear_session()
    assert await memory.get_items() == []
    assert await memory.pop_item() is None


async def test_reads_are_repaired_but_stored_rows_are_not(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    old = SessionMemory("s", db, known_tools={"old_tool"})
    await old.add_items([user("go"), call("c1", "old_tool"), output("c1")])

    renamed = SessionMemory("s", db, known_tools={"new_tool"})
    items = await renamed.get_items()
    assert [i.get("type", i.get("role")) for i in items] == ["user", "assistant"]
    assert renamed.last_repair.retired_tools == {"old_tool"}

    back = SessionMemory("s", db, known_tools={"old_tool"})
    assert [i.get("type", i.get("role")) for i in await back.get_items()] == [
        "user",
        "function_call",
        "function_call_output",
    ]


def test_the_cut_lands_on_a_user_message_and_keeps_recent_turns() -> None:
    items = [
        user("1"), call("a"), output("a"), assistant("x"),
        user("2"), call("b"), output("b"),
        user("3"), assistant("y"),
    ]  # fmt: skip
    assert find_cut(items, keep_recent_user_turns=2) == 4
    assert find_cut(items, keep_recent_user_turns=1) == 7
    assert find_cut(items, keep_recent_user_turns=3) == 0


async def test_compaction_replaces_old_turns_with_a_summary() -> None:
    memory = SessionMemory("s", Database(), threshold_tokens=200, keep_recent_user_turns=1)
    await memory.add_items([user("first " + LONG), call("a"), output("a"), assistant("done a")])
    await memory.add_items([user("second " + LONG), assistant("done b")])
    await memory.add_items([user("latest"), assistant("ok")])
    seen: list[list[dict]] = []

    async def summarise(items: list[dict]) -> str:
        seen.append(items)
        return "User asked twice; brief fetched."

    outcome = await memory.maybe_compact(summarise)

    assert outcome.status == "compacted"
    assert outcome.replaced_items == 6
    assert outcome.tokens_after < outcome.tokens_before
    assert len(seen[0]) == 6
    items = await memory.get_items()
    assert items[0] == assistant(SUMMARY_PREFIX + "User asked twice; brief fetched.")
    assert items[1:] == [user("latest"), assistant("ok")]
    assert memory.compactions() == 1

    await memory.add_items([user("after")])
    assert (await memory.get_items())[-1] == user("after")


async def test_under_the_threshold_nothing_happens() -> None:
    memory = SessionMemory("s", Database(), threshold_tokens=10_000)
    await memory.add_items([user("a"), user("b"), user("c")])

    async def never(_: list[dict]) -> str:
        raise AssertionError("should not be called")

    assert (await memory.maybe_compact(never)).status == "skipped"
    assert (await memory.maybe_compact(None)).status == "skipped"


async def test_a_failed_summary_changes_nothing_and_the_next_attempt_succeeds() -> None:
    memory = SessionMemory("s", Database(), threshold_tokens=100, keep_recent_user_turns=1)
    await memory.add_items([user("old " + LONG), assistant("x"), user("new"), assistant("y")])
    before = await memory.get_items()

    async def broken(_: list[dict]) -> str:
        raise TimeoutError("summariser timed out")

    outcome = await memory.maybe_compact(broken)
    assert outcome.status == "failed"
    assert "timed out" in outcome.reason
    assert await memory.get_items() == before

    async def working(_: list[dict]) -> str:
        return "old stuff"

    assert (await memory.maybe_compact(working)).status == "compacted"
    assert (await memory.get_items())[0]["content"].endswith("old stuff")


async def test_a_run_on_compacted_memory_sees_the_summary_first() -> None:
    memory = SessionMemory("s", Database(), threshold_tokens=100, keep_recent_user_turns=1)
    await memory.add_items([user("old " + LONG), assistant("x"), user("recent"), assistant("y")])

    async def summarise(_: list[dict]) -> str:
        return "earlier: user wanted a playful tone"

    await memory.maybe_compact(summarise)
    model = FakeModel([reply("noted")])
    result = await Runner.run(
        Agent(name="a", instructions="i", model=model), "next", session=memory
    )

    assert result.final_output == "noted"
    seen = model.calls[0].input
    assert isinstance(seen, list)
    assert "playful tone" in str(seen[0])
    assert seen[-1]["content"] == "next"
    assert (await memory.get_items())[-1]["role"] == "assistant"
