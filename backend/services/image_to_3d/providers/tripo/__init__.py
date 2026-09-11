import asyncio
from pathlib import Path

from ...base import ImageTo3DError, ImageTo3DProvider, Model3DAsset, Model3DJob, Model3DPollResult
from ...registry import register
from . import client
from .client import TripoApiError, TripoTaskFailed

# Tripo 任务状态 → Model3DPollResult.status；未知值继续轮询。
_STATUS_MAP: dict[str, str] = {
    "queued": "queued",
    "running": "in_progress",
    "success": "completed",
    "failed": "failed",
    "cancelled": "failed",
    "banned": "failed",
}

_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class TripoImageTo3DProvider(ImageTo3DProvider):
    provider_name = "tripo"
    SUPPORTS_RIGGING = True
    SUPPORTS_MULTIVIEW = True
    SUPPORTS_NEGATIVE_PROMPT = True
    SUPPORTS_ANIMATE_BIND = True

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = base_url

    async def _upload(self, path: Path) -> str:
        image_bytes = await asyncio.to_thread(path.read_bytes)
        return await client.upload_file(image_bytes, path.name, _CONTENT_TYPES.get(path.suffix.lower(), "image/png"))

    async def create_image_to_model(
        self,
        image_path: Path,
        *,
        multiview_paths: dict[str, Path] | None = None,
    ) -> Model3DJob:
        try:
            auxiliary_paths = {key: path for key, path in (multiview_paths or {}).items() if key != "front"}
            front_token = await self._upload(image_path)
            if self.SUPPORTS_MULTIVIEW and auxiliary_paths:
                # multiview 端点接受 MV 专属 framing hints；单图端点不接受。
                auxiliary_tokens = {key: await self._upload(path) for key, path in auxiliary_paths.items()}
                task_id = await client.create_image_to_model(
                    front_token,
                    multiview_tokens=auxiliary_tokens,
                    **client.tripo_common_kwargs_from_settings(
                        texture_alignment="original_image",
                        orientation="align_image",
                    ),
                )
            else:
                task_id = await client.create_image_to_model(front_token, **client.tripo_common_kwargs_from_settings())
            return Model3DJob(job_id=task_id)
        except TripoApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc

    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        try:
            data = await client.get_task(job.job_id)
        except TripoApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc
        status = _STATUS_MAP.get(data.get("status") or "", "in_progress")
        if status == "completed":
            url = (data.get("output") or {}).get("model_url") or ""
            return Model3DPollResult(
                status="completed",
                progress=100,
                assets=(Model3DAsset(kind="glb", url=url),) if url else (),
            )
        if status == "failed":
            return Model3DPollResult(status="failed", error=data.get("message") or str(data))
        return Model3DPollResult(status=status, progress=int(data.get("progress") or 0))

    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        urls = [a.url for a in result.assets if a.url]
        if not urls:
            raise ImageTo3DError("tripo task completed without a model_url", provider=self.provider_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "tripo_model.glb"
        await asyncio.to_thread(dest.write_bytes, await client.download_model(urls[0]))
        return dest

    async def rig_supported(self, job_id: str) -> bool:
        try:
            return bool((await client.poll_rig_check(await client.rig_check(job_id))).get("riggable"))
        except (TripoApiError, TripoTaskFailed) as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc

    async def start_rig(self, job_id: str, rig_type: str) -> Model3DJob:
        try:
            return Model3DJob(job_id=await client.rig(job_id, rig_type))
        except TripoApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc

    async def start_animate_bind(self, job_id: str, rig_type: str) -> Model3DJob:
        try:
            return Model3DJob(job_id=await client.retarget(job_id, rig_type))
        except (TripoApiError, ValueError) as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc

    def animation_clips(self, rig_type: str) -> dict[str, str]:
        return client.retarget_clips(rig_type)


register("tripo", TripoImageTo3DProvider)

__all__ = ["TripoImageTo3DProvider"]
