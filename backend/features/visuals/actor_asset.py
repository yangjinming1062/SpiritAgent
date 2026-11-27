"""将透明主体视频和播放所需的逐帧遮罩、锚点作为同一份资产交付。"""

from pathlib import Path

from infrastructure.assets.asset_store import compute_file_sha256, save_companion_asset_async
from infrastructure.async_io import run_blocking
from infrastructure.temp_files import media_work_directory
from infrastructure.video_processing.actor_clip import prepare_actor_clip

from .schemas import ActorClipBody, VisualAsset


async def prepare_actor_asset(source: Path, user_id: int, directory: str, *, preserve_alpha: bool) -> VisualAsset:
    async with media_work_directory(user_id) as root:
        media = await run_blocking(
            prepare_actor_clip,
            source,
            dst=root / "clip.webm",
            poster_dst=root / "poster.webp",
            mask_dst=root / "mask.json",
            first_frame_dst=root / "first.png",
            last_frame_dst=root / "last.png",
            preserve_alpha=preserve_alpha,
        )
        paths = {}
        for role, path in (
            ("video", media.video_path),
            ("poster", media.poster_path),
            ("mask", media.mask_path),
            ("first", media.first_frame_path),
            ("last", media.last_frame_path),
        ):
            paths[role] = await save_companion_asset_async(
                path, user_id=user_id, label=f"actor_{role}", ext=path.suffix[1:], directory=directory
            )
        first_hash = await run_blocking(compute_file_sha256, media.first_frame_path)
    return VisualAsset(
        media_kind="video",
        media_path=paths["video"],
        poster_path=paths["poster"],
        width=media.width,
        height=media.height,
        duration_ms=media.duration_ms,
        body=ActorClipBody(
            mask_path=paths["mask"],
            first_frame_path=paths["first"],
            last_frame_path=paths["last"],
            foot_anchor=media.foot_anchor,
            body_bounds=media.body_bounds,
            hit_bounds=media.hit_bounds,
            continuity_key=first_hash,
        ),
    )
