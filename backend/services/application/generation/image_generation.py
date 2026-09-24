import asyncio
import base64

from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, SESSION_LOCAL, download_capped, get_logger, save_file
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import asset_store, save_companion_asset_async, sniff_media_ext
from services.infrastructure.llm import (
    SIZE_TO_ASPECT,
    ClassifiedError,
    FailoverReason,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    classify_api_error,
    execute_with_fallback,
    resolve,
    resolve_provider_chain,
)

logger = get_logger(__name__)

_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


class ImageGenerationError(Exception):
    """生图执行失败；str(exc) 可给工具 JSON / 调用方展示。"""

    def __init__(
        self,
        message: str,
        *,
        internal: str | None = None,
        result_unknown: bool = False,
        can_fallback: bool = False,
    ) -> None:
        super().__init__(message)
        self.internal = internal or message
        self.result_unknown = result_unknown
        self.can_fallback = can_fallback
        self.classified: ClassifiedError | None = None


async def resolve_image_gen_chain(
    db: AsyncSession | None,
    user_id: int | None,
    reference_image: str | None,
    *,
    image_edit: bool = False,
    multiple_references: bool = False,
    background: str | None = None,
) -> tuple[list[ProviderConfig], str | None]:
    """在传入 reference_image 时按图生图能力过滤 image_gen 供应商链；image_edit 时改按图像编辑能力过滤。

    ``background="transparent"`` 时只保留声明原生透明输出的供应商。
    """
    full = await resolve_provider_chain(db, user_id, "image_gen")

    def _supports(cfg: ProviderConfig) -> bool:
        cls = resolve(ServiceType.image_gen, cfg.provider_name)
        if background == "transparent" and not cls.supports_transparent_background:
            return False
        if not reference_image:
            return True
        if image_edit and not cls.supports_image_edit:
            return False
        if not image_edit and not cls.supports_reference_image:
            return False
        return not multiple_references or cls.supports_multiple_reference_images

    capable = [c for c in full if _supports(c)]
    if not reference_image and background != "transparent":
        return full, None
    if full and not capable:
        if background == "transparent" and not reference_image:
            error = "当前图片生成供应商均不支持原生透明背景，请启用 local"
        elif multiple_references:
            error = "当前图片生成供应商不支持分别输入两张参考图，请配置支持双图的供应商"
        elif image_edit:
            error = "当前图片生成供应商均不支持图像编辑，请启用 gemini / grok / local 其中之一"
        else:
            error = "当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok / qwen / local 其中之一"
        return capable, error
    return capable, None


async def _persist_user_asset_async(data: bytes, user_id: int) -> str:
    ext = sniff_media_ext(data)
    if ext not in ("png", "jpg", "webp", "gif"):
        raise ImageGenerationError("图片生成服务返回了无效的图片数据，请重试")
    return await save_companion_asset_async(data, user_id=user_id, label="chat_image", ext=ext)


async def generate_images(
    prompt: str,
    *,
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    persist_user_assets: bool = False,
    image_edit: bool = False,
    provider_config: ProviderConfig | None = None,
    defer_storage: bool = False,
    background: str | None = None,
) -> list[str]:
    """走 image_gen 供应商链生成图片并落盘；成功返回 URL 列表，失败抛 ImageGenerationError。

    ``persist_user_assets=True`` 且提供 ``user_id`` 时，结果转存为 ``companion-assets/{user_id}/`` 永久资产并返回裸路径；否则落 temp-media（或透传供应商 URL）。
    ``image_edit=True`` 时 reference_image 是编辑底图，供应商链按图像编辑能力过滤；编辑不接受双参考拼图，
    secondary 与 image_edit 同给视为调用方违约，立即报错而非静默丢弃。
    ``defer_storage=True`` 返回原生 URL / data URI，由质量编排先保存结果再下载转存。
    ``background="transparent"`` 请求原生透明 PNG，链上只保留已验证 alpha 输出的供应商（如 local）。
    """
    if defer_storage and persist_user_assets:
        raise ValueError("defer_storage and persist_user_assets are mutually exclusive")
    if image_edit and secondary_reference_image:
        raise ImageGenerationError(
            "图像编辑不支持附加参考图，请改用重新生成",
            internal="image_edit with secondary reference",
        )
    try:
        if secondary_reference_image and not reference_image:
            raise ImageGenerationError("第二参考图需要同时提供身份参考图")
        if provider_config is not None:
            provider_cls = resolve(ServiceType.image_gen, provider_config.provider_name)
            if background == "transparent" and not provider_cls.supports_transparent_background:
                raise ImageGenerationError(
                    "当前图片生成供应商不支持原生透明背景",
                    internal=f"{provider_config.provider_name} does not support transparent output",
                )
            chain, err = [provider_config], None
        elif user_id is not None:
            async with SESSION_LOCAL() as db:
                chain, err = await resolve_image_gen_chain(
                    db,
                    user_id,
                    reference_image,
                    image_edit=image_edit,
                    multiple_references=bool(secondary_reference_image),
                    background=background,
                )
        else:
            chain, err = await resolve_image_gen_chain(
                None,
                None,
                reference_image,
                image_edit=image_edit,
                multiple_references=bool(secondary_reference_image),
                background=background,
            )
        if err:
            logger.warning("image generation chain error", extra={"error": err, "user_id": user_id})
            raise ImageGenerationError(err, internal=err)
        active_provider: list[str] = []

        async def _generate_call(p: ImageGenProvider) -> ImageGenResult:
            prov_name = getattr(getattr(p, "config", None), "provider_name", None) or getattr(
                p,
                "provider_name",
                type(p).__name__,
            )
            active_provider.append(prov_name)
            return await p.generate(
                ImageGenRequest(
                    prompt=prompt,
                    size=size,
                    aspect_ratio=size if ":" in size else SIZE_TO_ASPECT.get(size),
                    n=n,
                    reference_image=reference_image,
                    secondary_reference_image=secondary_reference_image,
                    response_format="url" if defer_storage else "b64",
                    background="transparent" if background == "transparent" else None,
                ),
            )

        result = await execute_with_fallback(None, user_id, "image_gen", call_fn=_generate_call, _chain=chain)
    except ImageGenerationError:
        raise
    except MissingLlmConfigError as e:
        logger.warning("image generation missing config", extra={"error": str(e), "user_id": user_id})
        raise ImageGenerationError("图片生成服务未配置", internal=str(e)) from e
    except Exception as e:
        logger.exception("image generation failed", extra={"user_id": user_id})
        classified = getattr(e, "classified", None) or classify_api_error(e)
        unknown = classified.reason == FailoverReason.result_unknown
        message = "图片生成结果未知，请核对供应商任务后再决定是否重做" if unknown else "图片生成失败，请稍后重试"
        error = ImageGenerationError(message, internal=str(e), result_unknown=unknown)
        error.classified = classified
        raise error from e

    if not result.images:
        raise ImageGenerationError("图片生成服务返回空结果", can_fallback=True)

    urls: list[str] = []
    as_user_assets = persist_user_assets and user_id is not None
    try:
        for asset in result.images:
            if asset.url:
                if as_user_assets:
                    # 供应商地址短时效：下载→魔数校验→转存正式资产，失败即本轮报错重试，不把短效 URL 落库。
                    data = await download_capped(asset.url, max_bytes=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, timeout=360.0)
                    urls.append(await _persist_user_asset_async(data, user_id))
                else:
                    urls.append(asset.url)
            elif asset.b64 is not None:
                if not asset.b64:
                    logger.warning("image asset has empty b64; skipping", extra={"mime": asset.mime})
                    continue
                if defer_storage:
                    urls.append(f"data:{asset.mime or 'image/jpeg'};base64,{asset.b64}")
                    continue
                data = await asyncio.to_thread(base64.b64decode, asset.b64)
                if as_user_assets:
                    urls.append(await _persist_user_asset_async(data, user_id))
                    continue
                ext = _EXT_BY_MIME.get((asset.mime or "").lower(), "jpg")
                _file_id, public_url = await asyncio.to_thread(
                    save_file,
                    data,
                    session_id="",
                    content_type=asset.mime or "image/jpeg",
                    ext=ext,
                )
                urls.append(public_url)
    except BaseException as exc:
        if as_user_assets:
            for url in urls:
                await asyncio.to_thread(asset_store.unlink_companion_asset, url)
        if not isinstance(exc, Exception) or isinstance(exc, ImageGenerationError):
            raise
        logger.warning("generated image storage failed", extra={"user_id": user_id}, exc_info=True)
        raise ImageGenerationError("生成图片无法保存，请稍后重试", internal=str(exc)) from exc
    if not urls:
        raise ImageGenerationError("图片生成服务返回空结果", can_fallback=True)
    used_provider = active_provider[-1] if active_provider else None
    logger.info(
        "Generated images",
        extra={"image_count": len(urls), "prompt": prompt, "provider": used_provider, "user_id": user_id},
    )
    return urls
