"""Session assets: identity by source, archive as a record.

Two rules shape everything here.

**Identity is the source, not the name.** Fetching the same page twice, or re-running
a tool on the same input, yields the same ``source_id``. Among a session's active
assets a source appears once: registering it again updates that entry instead of
adding a near-duplicate the model then has to tell apart.

**Archiving is a record, not a delete, and it is final.** An archived asset keeps its
row, with when, why and which message did it. It can no longer be used, and it
cannot be restored: the database refuses both a restore and a delete. If the user
wants the same material back, it comes back as a new asset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ArchiveReason(StrEnum):
    USER_REQUEST = "user_request"
    REPLACED = "replaced"
    PANEL = "panel"


class ArchiveRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    archived_at: str
    reason: ArchiveReason
    message_id: str | None = None
    note: str | None = None


class NewAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    source_id: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=1000)
    ref_id: str | None = None


class AssetPointer(BaseModel):
    """What a model is shown: enough to choose, never the content."""

    name: str
    kind: str
    summary: str
    source_id: str


class Asset(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    name: str
    kind: str
    source_id: str
    summary: str
    ref_id: str | None
    created_at: str
    updated_at: str
    registered_by_message: str | None
    archive: ArchiveRecord | None = None

    @property
    def active(self) -> bool:
        return self.archive is None

    def pointer(self) -> AssetPointer:
        return AssetPointer(
            name=self.name, kind=self.kind, summary=self.summary, source_id=self.source_id
        )


@dataclass(frozen=True)
class RegisterOutcome:
    asset: Asset
    created: bool


@dataclass(frozen=True)
class ArchiveOutcome:
    archived: list[Asset] = field(default_factory=list)
    already_archived: list[Asset] = field(default_factory=list)
    dependent_stages: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnAssetChanges:
    """What one user message did to the asset pool, applied in one transaction."""

    message_id: str | None
    registered: list[RegisterOutcome] = field(default_factory=list)
    archive: ArchiveOutcome = field(default_factory=ArchiveOutcome)

    @property
    def empty(self) -> bool:
        return (
            not self.registered and not self.archive.archived and not self.archive.already_archived
        )
