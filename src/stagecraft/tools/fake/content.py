"""Four fake tools covering a generic content pipeline: brief → outline → draft → render.

They do no real work. What they demonstrate is the shape of a good tool: typed
parameters with descriptions, a small typed result, raw output kept in the
workspace, and an explicit hint about what to do next.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from pydantic import Field

from stagecraft.tools.fake.workspace import FakeWorkspace
from stagecraft.tools.registry import ToolRegistry, ToolSpec, tool
from stagecraft.tools.results import ToolResult

Tone = Literal["neutral", "playful", "formal"]
OutputFormat = Literal["html", "pdf", "video"]


class BriefResult(ToolResult):
    brief_id: str
    word_count: int
    summary: str
    next_step: str


class OutlineResult(ToolResult):
    outline_id: str
    brief_id: str
    section_count: int
    summary: str
    next_step: str


class DraftResult(ToolResult):
    draft_id: str
    outline_id: str
    word_count: int
    summary: str
    next_step: str


class RenderResult(ToolResult):
    render_id: str
    draft_id: str
    format: OutputFormat
    duration_ms: int
    summary: str
    next_step: str | None = None


def build_fake_tools(workspace: FakeWorkspace) -> list[ToolSpec]:
    """The four tools, bound to one workspace."""

    @tool
    def fetch_brief(
        source: Annotated[str, "A URL or a short description of the product or topic."],
    ) -> BriefResult:
        """Fetch a content brief from a source and store it in the workspace.

        Returns the brief id and a one-line summary; the full text stays in the
        workspace.
        """
        text = " ".join(f"Point {i} about {source}." for i in range(1, 41))
        brief_id = workspace.put("brief", {"source": source, "text": text})
        return BriefResult(
            brief_id=brief_id,
            word_count=len(text.split()),
            summary=f"Brief for {source}: 40 points captured.",
            next_step="Call write_outline with this brief_id.",
        )

    @tool
    def write_outline(
        brief_id: Annotated[str, "Id returned by fetch_brief."],
        sections: Annotated[int, Field(ge=1, le=10, description="How many sections to plan.")] = 3,
    ) -> OutlineResult:
        """Write a section outline for a stored brief."""
        brief = workspace.require(brief_id, "brief")
        headings = [f"Section {i}: angle {i} on {brief['source']}" for i in range(1, sections + 1)]
        outline_id = workspace.put("outline", {"brief_id": brief_id, "headings": headings})
        return OutlineResult(
            outline_id=outline_id,
            brief_id=brief_id,
            section_count=sections,
            summary=f"{sections} sections planned for {brief_id}.",
            next_step="Call write_draft with this outline_id.",
        )

    @tool
    def write_draft(
        outline_id: Annotated[str, "Id returned by write_outline."],
        tone: Annotated[Tone, "Voice of the draft."] = "neutral",
    ) -> DraftResult:
        """Write a full draft that follows a stored outline."""
        outline = workspace.require(outline_id, "outline")
        body = "\n\n".join(f"{h}\n" + " ".join(["lorem"] * 60) for h in outline["headings"])
        draft_id = workspace.put("draft", {"outline_id": outline_id, "tone": tone, "body": body})
        return DraftResult(
            draft_id=draft_id,
            outline_id=outline_id,
            word_count=len(body.split()),
            summary=f"{tone} draft with {len(outline['headings'])} sections.",
            next_step="Call render_output with this draft_id, or revise the outline.",
        )

    @tool
    async def render_output(
        draft_id: Annotated[str, "Id returned by write_draft."],
        format: Annotated[OutputFormat, "Target output format."] = "html",
    ) -> RenderResult:
        """Render a stored draft into the requested format. Slow: simulates a render job."""
        workspace.require(draft_id, "draft")
        await asyncio.sleep(workspace.render_delay_seconds)
        render_id = workspace.put("render", {"draft_id": draft_id, "format": format})
        return RenderResult(
            render_id=render_id,
            draft_id=draft_id,
            format=format,
            duration_ms=int(workspace.render_delay_seconds * 1000),
            summary=f"{format} rendered from {draft_id}.",
        )

    return [fetch_brief, write_outline, write_draft, render_output]


def build_fake_registry(
    workspace: FakeWorkspace | None = None,
) -> tuple[ToolRegistry, FakeWorkspace]:
    """A registry holding the four fake tools, plus the workspace they write to."""
    # `workspace or ...` would drop an empty workspace: FakeWorkspace defines __len__.
    workspace = FakeWorkspace() if workspace is None else workspace
    return ToolRegistry(build_fake_tools(workspace)), workspace
