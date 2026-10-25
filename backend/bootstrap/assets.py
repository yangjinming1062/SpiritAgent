"""Explicit business reference providers for account-scoped asset collection."""

from features.action_library import asset_references as action_library
from features.appearance import asset_references as appearance
from features.appearance.asset_bindings import appearance_asset_paths
from features.assets.contributions import AssetContribution, configure_asset_contributions
from features.conversation.asset_bindings import message_binding_paths, used_message_voice_ids
from features.media import asset_references as media
from features.media.voice_usage import set_voice_usage_reader
from features.posts import asset_references as posts
from features.scenes import asset_references as scenes
from features.visuals import asset_references as visuals
from runtime.events import asset_references as events


def register_asset_contributions() -> None:
    set_voice_usage_reader(used_message_voice_ids)
    configure_asset_contributions(
        (
            AssetContribution("conversation", message_binding_paths),
            AssetContribution("appearance", appearance_asset_paths, cleanup=appearance.cleanup_obsolete_assets),
            AssetContribution("action_library", action_library.live_asset_paths),
            AssetContribution("scenes", scenes.live_asset_paths),
            AssetContribution("visuals", visuals.live_asset_paths, cleanup=visuals.cleanup_obsolete_assets),
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
