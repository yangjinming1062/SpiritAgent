"""Explicit business reference providers for account-scoped asset collection."""

from features.action_library import asset_references as action_library
from features.appearance import asset_references as appearance
from features.appearance.asset_bindings import appearance_asset_paths
from features.assets.contributions import AssetContribution, configure_asset_contributions
from features.conversation.asset_bindings import message_binding_paths, used_message_voice_ids
from features.jobs.service import cleanup_job_assets, job_asset_paths
from features.life.episode_media import live_episode_media_asset_paths, pending_episode_video_job_ids
from features.media import asset_references as media
from features.media.voice_usage import set_voice_usage_reader
from features.scenes import asset_references as scenes
from features.visuals import asset_references as visuals
from runtime.events import asset_references as events
from sqlalchemy.ext.asyncio import AsyncSession


async def _episode_media_assets(db: AsyncSession, user_id: int) -> set[str]:
    pending = await pending_episode_video_job_ids(db, user_id)
    paths = await media.pending_video_delivery_asset_paths(db, user_id, pending)
    # 先读在途引用，再读正式引用，使两次查询之间完成的采纳仍能保护文件。
    paths |= await live_episode_media_asset_paths(db, user_id)
    return paths


def register_asset_contributions() -> None:
    set_voice_usage_reader(used_message_voice_ids)
    configure_asset_contributions(
        (
            AssetContribution("conversation", message_binding_paths),
            AssetContribution("jobs", job_asset_paths, cleanup=cleanup_job_assets),
            AssetContribution("life_episode_media", _episode_media_assets),
            AssetContribution("appearance", appearance_asset_paths, cleanup=appearance.cleanup_obsolete_assets),
            AssetContribution("action_library", action_library.live_asset_paths),
            AssetContribution("scenes", scenes.live_asset_paths),
            AssetContribution("visuals", visuals.live_asset_paths, cleanup=visuals.cleanup_obsolete_assets),
            AssetContribution(
                "media",
                media.live_asset_paths,
                cleanup=media.cleanup_obsolete_assets,
                forget=media.forget_deleted_assets,
            ),
            AssetContribution("events", events.live_asset_paths, cleanup=events.cleanup_obsolete_assets),
        )
    )
