import asyncio
import base64
import io

from components import SESSION_LOCAL, download_capped, get_logger, save_file
from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import save_companion_asset
from services.infrastructure.llm import (
    ImageGenRequest,
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    execute_with_fallback,
    resolve,
    resolve_provider_chain,
    resolve_reference_bytes,
)

logger = get_logger(__name__)

_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


class ImageGenerationError(Exception):
    """生图执行失败；str(exc) 可给工具 JSON / 调用方展示。"""

    def __init__(self, message: str, *, internal: str | None = None) -> None:
        super().__init__(message)
        self.internal = internal or message


async def resolve_image_gen_chain(
    db: AsyncSession | None,
    user_id: int | None,
    reference_image: str | None,
    *,
    preferred_provider: str | list[str] | None = None,
) -> tuple[list[ProviderConfig], str | None]:
    """在传入 reference_image 时按图生图能力过滤 image_gen 供应商链。"""
    full = await resolve_provider_chain(db, user_id, "image_gen")
    if preferred_provider:
        priority = [preferred_provider] if isinstance(preferred_provider, str) else list(preferred_provider)
        rank = {name: i for i, name in enumerate(priority)}
        full = sorted(full, key=lambda c: rank.get(c.provider_name, len(priority)))
    if not reference_image:
        return full, None
    capable = [c for c in full if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if full and not capable:
        return (capable, "当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一")
    return capable, None


def compose_image_references(primary: bytes, secondary: bytes) -> bytes:
    sheet = Image.new("RGB", (2048, 1088), "white")
    draw = ImageDraw.Draw(sheet)
    for index, raw in enumerate((primary, secondary)):
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            sheet.paste(image, (index * 1024 + (1024 - image.width) // 2, 64 + (1024 - image.height) // 2), image)
        draw.text((index * 1024 + 24, 20), f"REFERENCE {index + 1}", fill="black", font_size=24)
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


def _image_ext_from_bytes(data: bytes, fallback: str = "jpg") -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"GIF8"):
        return "gif"
    return fallback


def _persist_user_asset(data: bytes, user_id: int, *, mime: str = "") -> str:
    ext = _EXT_BY_MIME.get((mime or "").lower()) or _image_ext_from_bytes(data)
    return save_companion_asset(data, user_id=user_id, label="chat_image", ext=ext)


async def generate_images(
    prompt: str,
    *,
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    preferred_provider: str | list[str] | None = None,
    persist_user_assets: bool = False,
) -> list[str]:
    """走 image_gen 供应商链生成图片并落盘；成功返回 URL 列表，失败抛 ImageGenerationError。

    ``persist_user_assets=True`` 且提供 ``user_id`` 时，结果转存为 ``companion-assets/{user_id}/`` 永久资产并返回裸路径；否则落 temp-media（或透传供应商 URL）。
    """
    try:
        if reference_image and secondary_reference_image:
            primary, secondary = await asyncio.gather(
                resolve_reference_bytes(reference_image),
                resolve_reference_bytes(secondary_reference_image),
            )
            sheet = await asyncio.to_thread(compose_image_references, primary[0], secondary[0])
            reference_image = "data:image/png;base64," + base64.b64encode(sheet).decode("ascii")
            prompt = (
                "The single input is a reference sheet: reference 1 is the LEFT panel, reference 2 is the RIGHT panel. Generate ONE final image, not a sheet; omit panel labels. "
                + prompt
            )
        req = ImageGenRequest(prompt=prompt, size=size, n=n, reference_image=reference_image)
        if user_id is not None:
            async with SESSION_LOCAL() as db:
                chain, err = await resolve_image_gen_chain(
                    db,
                    user_id,
                    reference_image,
                    preferred_provider=preferred_provider,
                )
        else:
            chain, err = await resolve_image_gen_chain(
                None,
                None,
                reference_image,
                preferred_provider=preferred_provider,
            )
        if err:
            logger.warning("image generation chain error", extra={"error": err, "user_id": user_id})
            raise ImageGenerationError(err, internal=err)
        active_provider: list[str] = []

        async def _generate_call(p):
            prov_name = getattr(getattr(p, "config", None), "provider_name", None) or getattr(
                p,
                "provider_name",
                type(p).__name__,
            )
            active_provider.append(prov_name)
            return await p.generate(req)

        result = await execute_with_fallback(None, user_id, "image_gen", call_fn=_generate_call, _chain=chain)
    except ImageGenerationError:
        raise
    except MissingLlmConfigError as e:
        logger.warning("image generation missing config", extra={"error": str(e), "user_id": user_id})
        raise ImageGenerationError("图片生成服务未配置", internal=str(e)) from e
    except Exception as e:
        logger.exception("image generation failed", extra={"user_id": user_id})
        raise ImageGenerationError("图片生成失败，请稍后重试", internal=str(e)) from e

    if not result.images:
        raise ImageGenerationError("图片生成服务返回空结果")

    urls: list[str] = []
    as_user_assets = persist_user_assets and user_id is not None
    for asset in result.images:
        if asset.url:
            if as_user_assets:
                try:
                    data = await download_capped(asset.url, max_bytes=50 * 1024 * 1024, timeout=120.0)
                    urls.append(_persist_user_asset(data, user_id, mime=asset.mime or ""))
                    continue
                except Exception:
                    logger.warning(
                        "failed to re-host provider image url as user asset; keeping provider url",
                        extra={"user_id": user_id},
                        exc_info=True,
                    )
            urls.append(asset.url)
        elif asset.b64 is not None:
            if not asset.b64:
                logger.warning("image asset has empty b64; skipping", extra={"mime": asset.mime})
                continue
            data = base64.b64decode(asset.b64)
            if as_user_assets:
                urls.append(_persist_user_asset(data, user_id, mime=asset.mime or ""))
                continue
            ext = _EXT_BY_MIME.get((asset.mime or "").lower(), "jpg")
            _file_id, public_url = save_file(data, session_id="", content_type=asset.mime or "image/jpeg", ext=ext)
            urls.append(public_url)
    if not urls:
        raise ImageGenerationError("图片生成服务返回空结果")
    used_provider = active_provider[-1] if active_provider else None
    logger.info(
        "Generated images",
        extra={"image_count": len(urls), "prompt": prompt, "provider": used_provider, "user_id": user_id},
    )
    return urls
