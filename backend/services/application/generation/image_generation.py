import asyncio
import base64

from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, SESSION_LOCAL, download_capped, get_logger
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
    providers_supporting,
    resolve,
    resolve_provider_chain,
)

logger = get_logger(__name__)


class ImageGenerationError(Exception):
    """生图执行失败；str(exc) 可给工具 JSON / 调用方展示。"""

    def __init__(
        self,
        message: str,
        *,
        internal: str | None = None,
        result_unknown: bool = False,
        can_fallback: bool = False,
        size_mismatch: bool = False,
    ) -> None:
        super().__init__(message)
        self.internal = internal or message
        self.result_unknown = result_unknown
        self.can_fallback = can_fallback
        self.size_mismatch = size_mismatch
        self.classified: ClassifiedError | None = None


class ImageReviewUnavailableError(ImageGenerationError):
    """已保存候选尚未完成场景内容核查；仅重试分析，不重新提交生图。"""


async def resolve_image_gen_chain(
    db: AsyncSession,
    user_id: int,
    *,
    has_reference: bool,
    image_edit: bool = False,
    multiple_references: bool = False,
    prompt_chars: int = 0,
    environment_reference: bool = False,
) -> tuple[list[ProviderConfig], str | None]:
    """按参考图/图像编辑能力过滤 image_gen 链；给出 ``prompt_chars`` 时跳过提示词放不下的供应商。"""
    full = await resolve_provider_chain(db, user_id, "image_gen")

    def _fits(cfg: ProviderConfig) -> bool:
        limit = resolve(ServiceType.image_gen, cfg.provider_name).max_prompt_chars
        return limit is None or prompt_chars <= limit

    def _capable(name: str) -> bool:
        cls = resolve(ServiceType.image_gen, name)
        if not has_reference:
            return True
        if environment_reference and not cls.supports_environment_reference_image:
            return False
        if image_edit and not cls.supports_image_edit:
            return False
        if not image_edit and not cls.supports_reference_image:
            return False
        return not multiple_references or cls.supports_multiple_reference_images

    capable = [c for c in full if _fits(c) and _capable(c.provider_name)]
    if not full or (capable == full and not has_reference):
        return full, None
    if not any(_fits(c) for c in full):
        limit = max(resolve(ServiceType.image_gen, c.provider_name).max_prompt_chars or 0 for c in full)
        return (
            [],
            f"提示词共 {prompt_chars} 字符，超过当前图片生成供应商的上限（{limit} 字符），请缩短描述或启用其他供应商",
        )
    if not capable:
        # 可选供应商按能力声明列出，随注册与能力位变化，不手写名单。
        names = " / ".join(name for name in providers_supporting(ServiceType.image_gen) if _capable(name))
        action = (
            "环境参考图"
            if environment_reference
            else "分别输入两张参考图"
            if multiple_references
            else "图像编辑"
            if image_edit
            else "以图生图"
        )
        hint = f"请启用 {names} 其中之一" if names else "请配置具备该能力的供应商"
        return capable, f"当前图片生成供应商均不支持{action}，{hint}"
    return capable, None


async def _persist_user_asset_async(data: bytes, user_id: int) -> str:
    ext = sniff_media_ext(data)
    if ext not in ("png", "jpg", "webp", "gif"):
        raise ImageGenerationError("图片生成服务返回了无效的图片数据，请重试")
    return await save_companion_asset_async(data, user_id=user_id, label="chat_image", ext=ext)


async def generate_images(
    prompt: str,
    *,
    user_id: int,
    size: str = "1024x1024",
    n: int = 1,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    persist_user_assets: bool = False,
    image_edit: bool = False,
    provider_config: ProviderConfig | None = None,
    background: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    exact_size: bool = False,
) -> list[str]:
    """走 image_gen 链生成图片，成功返回地址列表。``persist_user_assets=True`` 转存为用户资产返回裸路径，否则返回供应商 URL/data URI；``image_edit`` 以 reference_image 为底图且不接受双参考（同给即报错）；``background="transparent"`` 只随 ``provider_config`` 使用，该家须已验证 alpha 输出。"""
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
            # 冻结链在调用前才补全提示词（如背景说明），按实际发送的长度核对该家上限，超出时可换下一家。
            limit = provider_cls.max_prompt_chars
            if limit is not None and len(prompt) > limit:
                raise ImageGenerationError(
                    "提示词超过当前图片生成供应商的长度上限",
                    internal=f"{provider_config.provider_name} prompt {len(prompt)} chars > {limit}",
                    can_fallback=True,
                )
            chain, err = [provider_config], None
        else:
            async with SESSION_LOCAL() as db:
                chain, err = await resolve_image_gen_chain(
                    db,
                    user_id,
                    has_reference=bool(reference_image),
                    image_edit=image_edit,
                    multiple_references=bool(secondary_reference_image),
                    prompt_chars=len(prompt),
                )
        if err:
            logger.warning("image generation chain error", extra={"error": err, "user_id": user_id})
            raise ImageGenerationError(err, internal=err)
        active_provider: str | None = None
        request_aspect = aspect_ratio
        if request_aspect is None and not exact_size:
            request_aspect = size if ":" in size else SIZE_TO_ASPECT.get(size)

        async def _generate_call(p: ImageGenProvider) -> ImageGenResult:
            nonlocal active_provider
            active_provider = p.config.provider_name
            return await p.generate(
                ImageGenRequest(
                    prompt=prompt,
                    size=size,
                    aspect_ratio=request_aspect,
                    resolution=resolution,
                    exact_size=exact_size,
                    n=n,
                    reference_image=reference_image,
                    secondary_reference_image=secondary_reference_image,
                    image_edit=image_edit,
                    response_format="b64" if persist_user_assets else "url",
                    background="transparent" if background == "transparent" else None,
                ),
            )

        result = await execute_with_fallback(chain, ImageGenProvider, _generate_call, user_id=user_id)
    except ImageGenerationError:
        raise
    except MissingLlmConfigError as e:
        logger.warning("image generation missing config", extra={"error": str(e), "user_id": user_id})
        raise ImageGenerationError("图片生成服务未配置", internal=str(e)) from e
    except Exception as e:
        logger.exception("image generation failed", extra={"user_id": user_id})
        classified = classify_api_error(e)
        unknown = classified.reason == FailoverReason.result_unknown
        message = "图片生成结果未知，请核对供应商任务后再决定是否重做" if unknown else "图片生成失败，请稍后重试"
        error = ImageGenerationError(message, internal=str(e), result_unknown=unknown)
        error.classified = classified
        raise error from e

    if not result.images:
        raise ImageGenerationError("图片生成服务返回空结果", can_fallback=True)

    urls: list[str] = []
    try:
        for asset in result.images:
            if asset.url:
                if persist_user_assets:
                    # 供应商地址短时效：下载→魔数校验→转存正式资产，失败即本轮报错重试，不把短效 URL 落库。
                    data = await download_capped(asset.url, max_bytes=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, timeout=360.0)
                    urls.append(await _persist_user_asset_async(data, user_id))
                else:
                    urls.append(asset.url)
            elif asset.b64 is not None:
                if not asset.b64:
                    logger.warning("image asset has empty b64; skipping", extra={"mime": asset.mime})
                    continue
                if persist_user_assets:
                    data = await asyncio.to_thread(base64.b64decode, asset.b64)
                    urls.append(await _persist_user_asset_async(data, user_id))
                else:
                    urls.append(f"data:{asset.mime or 'image/jpeg'};base64,{asset.b64}")
    except BaseException as exc:
        if persist_user_assets:
            for url in urls:
                await asyncio.to_thread(asset_store.unlink_companion_asset, url)
        if not isinstance(exc, Exception) or isinstance(exc, ImageGenerationError):
            raise
        logger.warning("generated image storage failed", extra={"user_id": user_id}, exc_info=True)
        raise ImageGenerationError("生成图片无法保存，请稍后重试", internal=str(exc)) from exc
    if not urls:
        raise ImageGenerationError("图片生成服务返回空结果", can_fallback=True)
    logger.info(
        "Generated images",
        extra={"image_count": len(urls), "prompt_chars": len(prompt), "provider": active_provider, "user_id": user_id},
    )
    return urls
