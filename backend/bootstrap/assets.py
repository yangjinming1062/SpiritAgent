"""Explicit business reference providers for account-scoped asset collection."""

from features.actions import asset_references as actions
from features.appearance import asset_references as appearance
from features.assets.contributions import AssetContribution, configure_asset_contributions
from features.conversation import asset_references as conversation
from features.desktop_life import asset_references as desktop_life
from features.media import asset_references as media
from features.posts import asset_references as posts
from features.scenes import asset_references as scenes
from runtime.events import asset_references as events


def register_asset_contributions() -> None:
    configure_asset_contributions(
        (
            AssetContribution("conversation", conversation.live_asset_paths),
            AssetContribution("appearance", appearance.live_asset_paths, cleanup=appearance.cleanup_obsolete_assets),
            AssetContribution(
                "actions",
                actions.live_asset_paths,
                protection=actions.protected_until,
                forget=actions.forget_deleted_assets,
            ),
            AssetContribution("scenes", scenes.live_asset_paths),
            AssetContribution("desktop_life", desktop_life.live_asset_paths),
            AssetContribution("posts", posts.live_asset_paths),
            AssetContribution(
                "media",
                media.live_asset_paths,
                cleanup=media.cleanup_obsolete_assets,
                forget=media.forget_deleted_assets,
            ),
            AssetContribution("events", events.live_asset_paths, cleanup=events.cleanup_obsolete_assets),
        ),
    )
