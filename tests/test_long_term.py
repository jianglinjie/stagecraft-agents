import asyncio

from stagecraft.db import Database
from stagecraft.memory import LongTermMemory

TRANSCRIPT = [{"role": "user", "content": "Make it playful, our readers are developers."}]


async def test_a_profile_is_rewritten_whole_not_appended() -> None:
    memory = LongTermMemory(Database())
    seen: list[tuple[str, str]] = []

    async def rewrite(current: str, transcript: str) -> str:
        seen.append((current, transcript))
        return (
            "- audience: developers\n- tone: playful"
            if not current
            else "- audience: developers\n- tone: formal"
        )

    first = await memory.extract("topic:articles", TRANSCRIPT, rewrite)
    second = await memory.extract("topic:articles", TRANSCRIPT, rewrite)

    assert (first.revision, second.revision) == (1, 2)
    assert second.content == "- audience: developers\n- tone: formal"
    assert "playful" not in second.content
    assert seen[1][0] == first.content
    assert "developers" in seen[0][1]


async def test_profiles_are_capped() -> None:
    memory = LongTermMemory(Database(), max_chars=20)

    async def verbose(_: str, __: str) -> str:
        return "x" * 500

    assert len((await memory.extract("t", TRANSCRIPT, verbose)).content) == 20


async def test_a_concurrent_rewrite_is_merged_not_overwritten() -> None:
    memory = LongTermMemory(Database())
    gate = asyncio.Event()

    async def slow(current: str, _: str) -> str:
        await gate.wait()
        return (current + "\n- from slow").strip()

    async def fast(current: str, _: str) -> str:
        return (current + "\n- from fast").strip()

    slow_task = asyncio.create_task(memory.extract("t", TRANSCRIPT, slow))
    await asyncio.sleep(0)
    await memory.extract("t", TRANSCRIPT, fast)
    gate.set()
    final = await slow_task

    assert final.revision == 2
    assert "from fast" in final.content and "from slow" in final.content
