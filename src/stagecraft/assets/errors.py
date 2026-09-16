"""Asset refusals, with the codes a model sees."""

from __future__ import annotations

from stagecraft.tools.results import StructuredToolError


class AssetError(StructuredToolError):
    pass


class AssetNotFound(AssetError):
    code = "not_found"


class AssetArchived(AssetError):
    code = "asset_archived"


class AssetsInUse(AssetError):
    code = "assets_in_use"
