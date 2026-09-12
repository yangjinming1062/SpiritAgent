import asyncio
import base64
import io
import zipfile
from pathlib import Path

from components import SETTINGS, log_paid_call

from ...base import ImageTo3DError, ImageTo3DProvider, Model3DAsset, Model3DJob, Model3DPollResult
from . import client
from .client import HunyuanApiError

# TokenHub 接受裸 base64；自定义代理需要 data-URI 时改为前缀
IMAGE_BASE64_PREFIX = ""

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

# TokenHub 任务状态 → Model3DPollResult.status；未知值继续轮询。
_STATUS_MAP: dict[str, str] = {
    "queued": "queued",
    "pending": "queued",
    "in_progress": "in_progress",
    "running": "in_progress",
    "completed": "completed",
    "succeeded": "completed",
    "failed": "failed",
}


class HunyuanImageTo3DProvider(ImageTo3DProvider):
    """腾讯混元生 3D（TokenHub OpenAI 兼容接入），支持单图与多视图。当前无云端 rig / animate-bind 端点——客户端可消费产物但仅显示静态模型(无法驱动骨骼动画)。"""

    provider_name = "hunyuan"
    SUPPORTS_RIGGING = False
    SUPPORTS_MULTIVIEW = True
    SUPPORTS_ANIMATE_BIND = False
    DEFAULT_MODEL = "hy-3d-3.1"

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or client.DEFAULT_BASE_URL).rstrip("/")

    @property
    def _model(self) -> str:
        return getattr(SETTINGS, "hunyuan_model_version", "") or self.DEFAULT_MODEL

    async def _read_b64(self, path: Path) -> str:
        if path.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ImageTo3DError(f"种子图格式不支持（{path.suffix}，允许 jpg/png/webp）", provider=self.provider_name)
        image_bytes = await asyncio.to_thread(path.read_bytes)
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise ImageTo3DError(f"种子图超过 10MB 上限（{len(image_bytes)} bytes）", provider=self.provider_name)
        return IMAGE_BASE64_PREFIX + base64.b64encode(image_bytes).decode("ascii")

    async def create_image_to_model(
        self,
        image_path: Path,
        *,
        multiview_paths: dict[str, Path] | None = None,
    ) -> Model3DJob:
        try:
            auxiliary_paths = {key: path for key, path in (multiview_paths or {}).items() if key != "front"}
            front_b64 = await self._read_b64(image_path)
            if self.SUPPORTS_MULTIVIEW and auxiliary_paths:
                auxiliary_images = {key: await self._read_b64(path) for key, path in auxiliary_paths.items()}
                task_id = await client.create_image_to_model(
                    front_b64,
                    multiview_images=auxiliary_images,
                    **client.hunyuan_common_kwargs_from_settings(),
                )
            else:
                task_id = await client.create_image_to_model(front_b64, **client.hunyuan_common_kwargs_from_settings())
            return Model3DJob(job_id=task_id)
        except HunyuanApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name, model=self._model) from exc
        except ValueError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name, model=self._model) from exc

    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        try:
            body = await client.get_task(job.job_id, model=self._model)
        except HunyuanApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name, model=self._model) from exc

        status = _STATUS_MAP.get(str(body.get("status", "")).lower(), "in_progress")
        if status == "completed":
            assets = tuple(
                Model3DAsset(
                    kind=str(item.get("type") or "").lower(),
                    url=str(item.get("url") or ""),
                    preview_image_url=item.get("preview_image_url"),
                )
                for item in body.get("data") or []
                if isinstance(item, dict) and item.get("url")
            )
            log_paid_call(
                self.provider_name,
                "image_to_3d_result",
                task_id=job.job_id,
                urls=[a.url for a in assets],
                level="debug",
            )
            return Model3DPollResult(status="completed", progress=100, assets=assets)
        if status == "failed":
            return Model3DPollResult(status="failed", error=str(body.get("error") or body.get("message") or body)[:500])
        return Model3DPollResult(status=status, progress=0)

    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        glb_urls = [a.url for a in result.assets if a.kind.lower() == "glb"]
        any_urls = [a.url for a in result.assets if a.url]
        if not any_urls:
            raise ImageTo3DError("hunyuan 任务完成但未返回模型下载地址", provider=self.provider_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        raw = await client.download_model(glb_urls[0] if glb_urls else any_urls[0])
        if raw[:4] == b"PK\x03\x04":
            glb_path = _extract_glb_from_zip(raw, dest_dir)
            if glb_path is None:
                raise ImageTo3DError("hunyuan 产物 zip 内未找到 GLB", provider=self.provider_name)
            return glb_path
        dest = dest_dir / "hunyuan_model.glb"
        await asyncio.to_thread(dest.write_bytes, raw)
        return dest


def _extract_glb_from_zip(raw: bytes, dest_dir: Path) -> Path | None:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".glb"):
                out = dest_dir / Path(name).name
                out.write_bytes(zf.read(name))
                return out
    return None


__all__ = ["HunyuanApiError", "HunyuanImageTo3DProvider"]
