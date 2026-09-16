"""Asset tools. Lookups by name go through ``AssetStore.resolve``; archiving is final."""

from __future__ import annotations

from typing import Annotated, Literal

from stagecraft.assets import ArchiveReason, AssetPointer, AssetsInUse, AssetStore
from stagecraft.plan import RoleDenied
from stagecraft.tools.context import RunContext
from stagecraft.tools.registry import ToolSpec, tool
from stagecraft.tools.results import ToolResult


class AssetList(ToolResult):
    assets: list[AssetPointer]
    archived_names: list[str]


class AssetDetail(ToolResult):
    asset: AssetPointer
    ref_id: str | None


class AssetsArchived(ToolResult):
    archived: list[str]
    already_archived: list[str]
    dependent_stages: dict[str, list[str]]
    next_action: str


def build_asset_tools(assets: AssetStore) -> list[ToolSpec]:
    @tool
    def asset_list(ctx: RunContext) -> AssetList:
        """List the session's active assets as pointers, plus names that were archived."""
        return AssetList(
            assets=[a.pointer() for a in assets.active(ctx.chat_id)],
            archived_names=[a.name for a in assets.archived(ctx.chat_id)],
        )

    @tool
    def asset_get(ctx: RunContext, name: Annotated[str, "Asset name or id."]) -> AssetDetail:
        """Look one active asset up by name. Archived assets are refused."""
        asset = assets.resolve(ctx.chat_id, name)
        return AssetDetail(asset=asset.pointer(), ref_id=asset.ref_id)

    @tool
    def archive_session_assets(
        ctx: RunContext,
        names: Annotated[list[str], "Names of the assets to archive."],
        reason: Annotated[
            Literal["user_request", "replaced"],
            "user_request: the user asked in chat. replaced: a new output supersedes it.",
        ],
        force: Annotated[
            bool, "Archive even if unfinished stages use them. Only after the user agreed."
        ] = False,
    ) -> AssetsArchived:
        """Archive assets. Final: an archived asset can never be used or restored."""
        if ctx.role != "orchestrator":
            raise RoleDenied(f"role {ctx.role!r} may not archive assets")
        targets = [a for a in assets.active(ctx.chat_id) if a.name in names or a.id in names]
        in_use = assets.dependents(ctx.chat_id, targets)
        if in_use and not force:
            raise AssetsInUse(
                "some assets are still used by unfinished stages",
                issues=[f"{name}: used by {', '.join(ids)}" for name, ids in in_use.items()],
                hint=(
                    "Name these stages to the user and replan them, or get the user's "
                    "confirmation and call again with force=true."
                ),
            )
        outcome = assets.archive(
            ctx.chat_id, names, reason=ArchiveReason(reason), message_id=ctx.message_id
        )
        affected = outcome.dependent_stages
        return AssetsArchived(
            archived=[a.name for a in outcome.archived],
            already_archived=[a.name for a in outcome.already_archived],
            dependent_stages=affected,
            next_action=(
                "tell the user which stages lost an input and replan them"
                if affected
                else "confirm the archive to the user"
            ),
        )

    return [asset_list, asset_get, archive_session_assets]
