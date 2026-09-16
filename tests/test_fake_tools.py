import time

from stagecraft.tools import ToolError
from stagecraft.tools.fake import (
    BriefResult,
    DraftResult,
    FakeWorkspace,
    OutlineResult,
    RenderResult,
    build_fake_registry,
)


async def test_pipeline_runs_end_to_end_with_ids_only() -> None:
    registry, workspace = build_fake_registry(FakeWorkspace(render_delay_seconds=0))
    assert registry.names() == ["fetch_brief", "write_outline", "write_draft", "render_output"]

    brief = await registry.get("fetch_brief").invoke({"source": "https://example.com/p/1"})
    assert isinstance(brief, BriefResult)
    assert brief.brief_id == "brief_0001"
    assert brief.summary and brief.next_step

    outline = await registry.get("write_outline").invoke({"brief_id": "brief_0001", "sections": 2})
    assert isinstance(outline, OutlineResult)
    assert outline.outline_id == "outline_0001"
    assert outline.section_count == 2

    draft = await registry.get("write_draft").invoke(
        {"outline_id": "outline_0001", "tone": "playful"}
    )
    assert isinstance(draft, DraftResult)
    assert draft.draft_id == "draft_0001"
    assert draft.word_count > 100

    render = await registry.get("render_output").invoke({"draft_id": "draft_0001", "format": "pdf"})
    assert isinstance(render, RenderResult)
    assert render.render_id == "render_0001"
    assert render.format == "pdf"

    assert workspace.ids() == ["brief_0001", "outline_0001", "draft_0001", "render_0001"]


async def test_results_carry_summaries_not_raw_content() -> None:
    registry, workspace = build_fake_registry()
    brief = await registry.get("fetch_brief").invoke({"source": "topic"})
    assert isinstance(brief, BriefResult)

    raw_text = workspace.require("brief_0001", "brief")["text"]
    assert brief.word_count == len(raw_text.split())
    assert raw_text not in brief.model_dump_json()
    assert len(brief.model_dump_json()) < len(raw_text)


async def test_unknown_ids_come_back_as_tool_errors() -> None:
    registry, _ = build_fake_registry()
    result = await registry.get("write_outline").invoke({"brief_id": "brief_9999"})
    assert isinstance(result, ToolError)
    assert result.code == "tool_failed"
    assert "brief_9999" in result.message


async def test_render_output_is_async_and_waits() -> None:
    registry, workspace = build_fake_registry(FakeWorkspace(render_delay_seconds=0.05))
    assert FakeWorkspace().render_delay_seconds == 1.0
    assert registry.get("render_output").is_async is True

    await registry.get("fetch_brief").invoke({"source": "s"})
    await registry.get("write_outline").invoke({"brief_id": "brief_0001"})
    await registry.get("write_draft").invoke({"outline_id": "outline_0001"})

    started = time.perf_counter()
    result = await registry.get("render_output").invoke({"draft_id": "draft_0001"})
    elapsed = time.perf_counter() - started

    assert isinstance(result, RenderResult)
    assert elapsed >= 0.05
    assert result.duration_ms == 50
    assert workspace.ids("render") == ["render_0001"]
