"""Fake tools for the generic content pipeline."""

from stagecraft.tools.fake.content import (
    BriefResult,
    DraftResult,
    OutlineResult,
    RenderResult,
    build_fake_registry,
    build_fake_tools,
)
from stagecraft.tools.fake.workspace import FakeWorkspace

__all__ = [
    "BriefResult",
    "DraftResult",
    "FakeWorkspace",
    "OutlineResult",
    "RenderResult",
    "build_fake_registry",
    "build_fake_tools",
]
