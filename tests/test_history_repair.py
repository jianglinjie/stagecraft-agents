from stagecraft.memory import estimate_tokens, repair_history


def call(call_id: str, name: str, args: str = "{}") -> dict:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": args}


def output(call_id: str, text: str = '{"status":"ok"}') -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": text}


USER = {"role": "user", "content": "hi"}


def test_healthy_history_is_left_alone() -> None:
    items = [USER, call("c1", "fetch_brief"), output("c1"), {"role": "assistant", "content": "ok"}]
    repaired, report = repair_history(items, {"fetch_brief"})
    assert repaired == items
    assert not report.changed


def test_a_retired_tool_call_becomes_a_note_that_keeps_what_happened() -> None:
    items = [
        USER,
        call("c1", "old_fetch", '{"source":"x"}'),
        output("c1", '{"brief_id":"brief_0001"}'),
    ]
    repaired, report = repair_history(items, {"fetch_brief"})

    assert report.retired_tools == {"old_fetch"}
    assert len(repaired) == 2
    note = repaired[1]
    assert note["role"] == "assistant"
    assert "old_fetch" in note["content"] and "no longer exists" in note["content"]
    assert '"source":"x"' in note["content"] and "brief_0001" in note["content"]


def test_a_call_cut_off_by_a_crash_becomes_a_note_instead_of_a_broken_request() -> None:
    items = [USER, call("c1", "render_output"), call("c2", "fetch_brief"), output("c2")]
    repaired, report = repair_history(items, {"render_output", "fetch_brief"})

    assert report.interrupted_calls == ["c1"]
    assert "interrupted before it returned" in repaired[1]["content"]
    assert repaired[2:] == [call("c2", "fetch_brief"), output("c2")]


def test_an_output_without_its_call_is_dropped() -> None:
    repaired, report = repair_history([USER, output("ghost")], {"fetch_brief"})
    assert repaired == [USER]
    assert report.orphan_outputs == ["ghost"]


def test_without_a_tool_list_only_structural_problems_are_repaired() -> None:
    items = [call("c1", "anything"), output("c1"), call("c2", "dangling")]
    repaired, report = repair_history(items, None)
    assert repaired[:2] == items[:2]
    assert report.retired_tools == set()
    assert report.interrupted_calls == ["c2"]


def test_long_tool_payloads_are_clipped_in_notes() -> None:
    items = [call("c1", "old", "x" * 5000), output("c1", "y" * 5000)]
    repaired, _ = repair_history(items, set())
    assert len(repaired[0]["content"]) < 1000


def test_token_estimate_grows_with_content() -> None:
    small = estimate_tokens([USER])
    large = estimate_tokens([USER, {"role": "user", "content": "word " * 400}])
    assert 0 < small < large
    assert 400 <= large <= 700
