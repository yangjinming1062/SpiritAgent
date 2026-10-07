"""图片链：角色图按身份评分择优，场景独立核查是否重复桌面伙伴。"""

import asyncio
import io
import json
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, SESSION_LOCAL, download_capped, get_logger
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.assets import asset_store, build_data_uri
from services.infrastructure.llm import (
    ASPECT_RATIOS,
    SIZE_TO_ASPECT,
    ProviderConfig,
    ServiceType,
    resolve,
    resolve_reference_bytes,
    select_image_canvas,
)
from services.infrastructure.video_processing import VideoProcessError, prepare_transparent_image

from .identity_review import score_character_image
from .image_generation import ImageGenerationError, generate_images, resolve_image_gen_chain
from .media_chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
)
from .scene_image_review import review_scene_image

logger = get_logger(__name__)

# 2% 容差覆盖 local 1792x1024 与 16:9 的差异，并拒绝 5:3。
_SIZE_ASPECT_TOLERANCE = 0.02


class CharacterImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["character"] = "character"
    prompt: str
    size: str
    n: int = Field(ge=1)
    reference_image: str
    secondary_reference_image: str | None = None
    image_edit: bool = False
    identity_reference: str
    identity_text: str = ""
    max_image_bytes: int = Field(default=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, gt=0)
    size_enforced: bool = False
    prefer_transparent_background: bool = False


class SceneImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["scene"] = "scene"
    prompt: str
    size: str
    n: int = Field(default=1, ge=1, le=1)
    target_width: int = Field(gt=0)
    target_height: int = Field(gt=0)
    reference_image: str | None = None
    max_image_bytes: int = Field(default=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, gt=0)
    size_enforced: bool = True


# 参考图字段与冻结标签（字段名 → 标签）：清理清单与冻结映射共用，新增参考字段只改这里。
_REFERENCE_FIELDS: dict[type, tuple[tuple[str, str], ...]] = {
    SceneImageInput: (("reference_image", "ref"),),
    CharacterImageInput: (
        ("reference_image", "ref"),
        ("secondary_reference_image", "ref2"),
        ("identity_reference", "identity"),
    ),
}


class ImageChainState(MediaChainState):
    storage_directory: str | None = None
    inputs: CharacterImageInput | SceneImageInput | None = Field(default=None, discriminator="purpose")
    pending_urls: list[str] = Field(default_factory=list)
    pending_slots: list[int] = Field(default_factory=list)
    pending_path: str | None = None
    remaining_slots: list[int] = Field(default_factory=list)
    size_rejected: int = 0

    def best(self, slot: int = 0) -> MediaCandidate | None:
        if isinstance(self.inputs, SceneImageInput):
            return next(
                (
                    candidate
                    for candidate in self.candidates
                    if candidate.slot == slot and candidate.evaluated and candidate.accepted is True
                ),
                None,
            )
        return super().best(slot)

    def needs_next(self, slot: int = 0) -> bool:
        if isinstance(self.inputs, SceneImageInput):
            return not self.stop_reason and self.next_index < len(self.providers) and self.best(slot) is None
        return super().needs_next(slot)

    def finish(self, slots: int = 1) -> None:
        if isinstance(self.inputs, SceneImageInput):
            if not self.stop_reason:
                self.stop_reason = (
                    "accepted" if all(self.best(slot) is not None for slot in range(slots)) else "exhausted"
                )
            self.phase = "complete"
        else:
            super().finish(slots)

    def stored_paths(self) -> set[str]:
        """已落盘的候选、未登记完成的转存与冻结输入路径，供所有者清理。"""
        fields = _REFERENCE_FIELDS.get(type(self.inputs), ()) if self.inputs is not None else ()
        frozen = [getattr(self.inputs, attr) for attr, _label in fields]
        return {
            self.pending_path or "",
            *(candidate.path for candidate in self.candidates),
            *(path for candidate in self.candidates for path in candidate.artifacts),
            *(path for path in frozen if path and asset_store.parse_companion_asset_path(path) is not None),
        } - {""}


ImageProgressWriter = Callable[[ImageChainState], Awaitable[None]]


def _expected_aspect_ratio(size: str, *, exact_size: bool = False) -> float | None:
    """请求 size（像素串或画幅标签）的目标宽高比；无法解析时不启用核对。"""
    text = size.strip()
    if text in ASPECT_RATIOS:
        return ASPECT_RATIOS[text]
    if ":" in text:
        try:
            width_s, height_s = text.split(":", 1)
            width, height = float(width_s), float(height_s)
            if width > 0 and height > 0:
                return width / height
        except ValueError:
            return None
    mapped = SIZE_TO_ASPECT.get(text) if not exact_size else None
    if mapped:
        return ASPECT_RATIOS[mapped]
    if "x" in text.lower():
        try:
            width_s, height_s = text.lower().split("x", 1)
            width, height = int(width_s), int(height_s)
            if width > 0 and height > 0:
                return width / height
        except ValueError:
            return None
    return None


def _size_matches_request(size: str, width: int, height: int, *, exact_size: bool = False) -> bool:
    expected = _expected_aspect_ratio(size, exact_size=exact_size)
    if expected is None or width <= 0 or height <= 0:
        return True
    actual = width / height
    return abs(actual - expected) <= expected * _SIZE_ASPECT_TOLERANCE


def _validate_image(
    data: bytes,
    *,
    size: str | None = None,
    size_enforced: bool = False,
    exact_size: bool = False,
) -> str:
    ext = asset_store.sniff_media_ext(data)
    if ext not in ("png", "jpg", "webp", "gif"):
        raise ImageGenerationError("供应商返回的图片无法读取", can_fallback=True)
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.width, image.height
    except Exception as exc:
        raise ImageGenerationError("供应商返回的图片无法读取", can_fallback=True) from exc
    if size_enforced and size and not _size_matches_request(size, width, height, exact_size=exact_size):
        logger.info(
            "image size gate rejected candidate",
            extra={"width": width, "height": height, "requested_size": size},
        )
        raise ImageGenerationError(
            f"生成图片画幅不符合要求（{width}x{height}）",
            can_fallback=True,
            size_mismatch=True,
        )
    return ext


async def image_asset_bytes(path: str, *, max_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES) -> tuple[bytes, str]:
    """读取图片链地址：companion-assets 裸路径、供应商 data URI 或原生 URL。"""
    parsed = asset_store.parse_companion_asset_path(path)
    if parsed is not None:
        local = asset_store.resolve_companion_asset_path(*parsed)
        if local is None:
            raise ImageGenerationError("生成图片无法读取")
        if local[0].stat().st_size > max_bytes:
            raise ImageGenerationError("生成图片超过大小限制", can_fallback=True)
        return await asyncio.to_thread(local[0].read_bytes), local[1]
    if path.startswith("data:"):
        try:
            data, mime = await resolve_reference_bytes(path)
        except ValueError as exc:
            raise ImageGenerationError("供应商返回的图片无法读取", can_fallback=True) from exc
    else:
        data = await download_capped(path, max_bytes=max_bytes, timeout=360.0)
        mime = asset_store.image_mime_for_extension(asset_store.sniff_media_ext(data) or "") or "image/jpeg"
    if len(data) > max_bytes:
        raise ImageGenerationError("生成图片超过大小限制", can_fallback=True)
    return data, mime


async def _materialize_reference(value: str | None, *, max_bytes: int) -> str | None:
    """提交与评分前把冻结路径还原为 data URI；空值与已是 data URI 的输入原样返回。"""
    if not value or value.startswith("data:"):
        return value
    data, mime = await image_asset_bytes(value, max_bytes=max_bytes)
    return await asyncio.to_thread(build_data_uri, data, mime)


async def _freeze_input_references(
    inputs: CharacterImageInput | SceneImageInput,
    *,
    user_id: int,
    generation_id: str,
    storage_directory: str,
) -> None:
    """落库链的参考图在冻结时落为链资产，状态只存裸路径；相同原值只落一份。"""
    fields = _REFERENCE_FIELDS[type(inputs)]
    frozen: dict[str, str] = {}
    for attr, label in fields:
        value = getattr(inputs, attr)
        if not value or value in frozen or asset_store.parse_companion_asset_path(value) is not None:
            continue
        data, _ = await image_asset_bytes(value, max_bytes=inputs.max_image_bytes)
        ext = asset_store.sniff_media_ext(data)
        if ext not in {"png", "jpg", "webp", "gif"}:
            raise ImageGenerationError("生成图片无法读取")
        frozen[value] = await asset_store.save_image_chain_input_asset_async(
            data,
            user_id=user_id,
            generation_id=generation_id,
            label=label,
            ext=ext,
            directory=storage_directory,
        )
    for attr, _label in fields:
        value = getattr(inputs, attr)
        if value:
            setattr(inputs, attr, frozen.get(value, value))


async def _save_progress(state: ImageChainState, writer: ImageProgressWriter | None) -> None:
    if writer is not None:
        await writer(state)


async def _complete_images(
    state: ImageChainState,
    writer: ImageProgressWriter | None,
) -> list[str]:
    if state.inputs is None:
        raise ImageGenerationError("图片任务缺少生成输入")
    selected = [candidate.path for slot in range(state.inputs.n) if (candidate := state.best(slot)) is not None]
    if not selected:
        if state.stop_reason == "result_unknown":
            raise ImageGenerationError(
                "图片生成结果未知，请核对供应商任务后再决定是否重做",
                result_unknown=True,
            )
        if state.size_rejected:
            raise ImageGenerationError("生成图片画幅不符合要求")
        if isinstance(state.inputs, SceneImageInput) and any(candidate.evaluated for candidate in state.candidates):
            raise ImageGenerationError("场景生成未通过内容检查，请调整环境描述后重试")
        raise ImageGenerationError("图片生成失败，未取得可用候选")
    state.finish(state.inputs.n)
    await _save_progress(state, writer)
    retained = set(selected) | {
        path for candidate in state.candidates if candidate.path in selected for path in candidate.artifacts
    }
    for path in state.stored_paths() - retained:
        await asyncio.to_thread(asset_store.unlink_companion_asset, path)
    logger.info(
        "image chain selected",
        extra={"selected": selected, "attempts": state.next_index, "stop_reason": state.stop_reason},
    )
    return selected


async def generate_character_images(
    prompt: str,
    *,
    user_id: int,
    storage_directory: str,
    reference_image: str,
    identity_reference: str,
    size: str = "1024x1024",
    n: int = 1,
    secondary_reference_image: str | None = None,
    image_edit: bool = False,
    identity_text: str = "",
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    store_attempts: int = 3,
    before_submit: Callable[[], Awaitable[None]] | None = None,
    max_image_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
    size_enforced: bool = False,
    prefer_transparent_background: bool = False,
) -> list[str]:
    """返回已保存的用户资产；恢复只消费冻结输入、已知结果和未提交的链尾。"""
    state = state if state is not None else ImageChainState()
    if not storage_directory:
        raise ValueError("image task requires a storage directory")
    if state.storage_directory is None:
        state.storage_directory = storage_directory
    elif state.storage_directory != storage_directory:
        raise ImageGenerationError("图片任务存储归属已变化")
    configs: dict[int, ProviderConfig] = {}
    if state.inputs is None:
        if image_edit and secondary_reference_image:
            raise ImageGenerationError("图像编辑不支持附加参考图，请改用重新生成")
        state.inputs = CharacterImageInput(
            prompt=prompt,
            size=size,
            n=n,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            image_edit=image_edit,
            identity_reference=identity_reference,
            identity_text=identity_text,
            max_image_bytes=max_image_bytes,
            size_enforced=size_enforced,
            prefer_transparent_background=prefer_transparent_background,
        )
        async with SESSION_LOCAL() as db:
            chain, error = await resolve_image_gen_chain(
                db,
                user_id,
                has_reference=bool(reference_image),
                image_edit=image_edit,
                multiple_references=bool(secondary_reference_image),
                prompt_chars=len(prompt),
            )
        if error or not chain:
            raise ImageGenerationError(error or "图片生成服务未配置")
        if prefer_transparent_background:
            # 原生透明优先；组内保持用户链序，非透明供应商保留为安全回退。
            chain.sort(
                key=lambda config: (
                    not resolve(ServiceType.image_gen, config.provider_name).supports_transparent_background
                ),
            )
        state.providers = [FrozenMediaProvider.from_config(config) for config in chain]
        for provider in state.providers:
            provider_cls = resolve(ServiceType.image_gen, provider.provider)
            provider.max_images_per_request = provider_cls.max_images_per_request
            if prefer_transparent_background and provider_cls.supports_transparent_background:
                provider.background = "transparent"
        configs = dict(enumerate(chain))
        if save_progress is not None:
            await _freeze_input_references(
                state.inputs,
                user_id=user_id,
                generation_id=state.generation_id,
                storage_directory=storage_directory,
            )
        await _save_progress(state, save_progress)
    if not isinstance(state.inputs, CharacterImageInput):
        raise ImageGenerationError("图片任务类型与角色生成不符")
    return await _run_image_chain(
        state,
        user_id=user_id,
        configs=configs,
        save_progress=save_progress,
        store_attempts=store_attempts,
        before_submit=before_submit,
    )


async def generate_scene_images(
    prompt: str,
    *,
    user_id: int,
    storage_directory: str,
    target_width: int,
    target_height: int,
    reference_image: str | None = None,
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    store_attempts: int = 3,
    before_submit: Callable[[], Awaitable[None]] | None = None,
    max_image_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
) -> list[str]:
    """返回通过伙伴重复出镜检查的原始用户资产；画布和每家尝试随任务冻结。"""
    state = state if state is not None else ImageChainState()
    if not storage_directory:
        raise ValueError("image task requires a storage directory")
    if state.storage_directory is None:
        state.storage_directory = storage_directory
    elif state.storage_directory != storage_directory:
        raise ImageGenerationError("图片任务存储归属已变化")
    configs: dict[int, ProviderConfig] = {}
    if state.inputs is None:
        state.inputs = SceneImageInput(
            prompt=prompt,
            size=f"{target_width}x{target_height}",
            target_width=target_width,
            target_height=target_height,
            reference_image=reference_image,
            max_image_bytes=max_image_bytes,
        )
        async with SESSION_LOCAL() as db:
            chain, error = await resolve_image_gen_chain(
                db,
                user_id,
                has_reference=bool(reference_image),
                prompt_chars=len(prompt),
                environment_reference=True,
            )
        if error or not chain:
            raise ImageGenerationError(error or "图片生成服务未配置")
        for config in chain:
            provider = FrozenMediaProvider.from_config(config)
            provider.max_images_per_request = resolve(
                ServiceType.image_gen,
                config.provider_name,
            ).max_images_per_request
            canvas = select_image_canvas(config, target_width, target_height)
            provider.image_size = canvas.size
            provider.image_aspect_ratio = canvas.aspect_ratio
            provider.image_resolution = canvas.resolution
            provider.image_exact_size = canvas.exact_size
            state.providers.append(provider)
        configs = dict(enumerate(chain))
        if save_progress is not None:
            await _freeze_input_references(
                state.inputs,
                user_id=user_id,
                generation_id=state.generation_id,
                storage_directory=storage_directory,
            )
        await _save_progress(state, save_progress)
    if not isinstance(state.inputs, SceneImageInput):
        raise ImageGenerationError("图片任务类型与场景生成不符")
    return await _run_image_chain(
        state,
        user_id=user_id,
        configs=configs,
        save_progress=save_progress,
        store_attempts=store_attempts,
        before_submit=before_submit,
    )


async def _run_image_chain(
    state: ImageChainState,
    *,
    user_id: int,
    configs: dict[int, ProviderConfig],
    save_progress: ImageProgressWriter | None,
    store_attempts: int,
    before_submit: Callable[[], Awaitable[None]] | None,
) -> list[str]:
    inputs = state.inputs
    if inputs is None:
        raise ImageGenerationError("图片任务缺少生成输入")
    scene = isinstance(inputs, SceneImageInput)
    if state.phase == "submitting":
        state.stop_reason = "result_unknown"
        await _save_progress(state, save_progress)
        return await _complete_images(state, save_progress)
    if state.phase == "complete":
        return await _complete_images(state, save_progress)
    # 参考图在链运行内不变：物化一次供提交与评分复用，避免逐候选重复读盘/解码。
    reference_uri = await _materialize_reference(inputs.reference_image, max_bytes=inputs.max_image_bytes)
    secondary_uri = (
        await _materialize_reference(inputs.secondary_reference_image, max_bytes=inputs.max_image_bytes)
        if isinstance(inputs, CharacterImageInput)
        else None
    )
    identity_uri = (
        await _materialize_reference(inputs.identity_reference, max_bytes=inputs.max_image_bytes)
        if isinstance(inputs, CharacterImageInput)
        else None
    )
    try:
        while True:
            if state.phase == "storing":
                # 供应商地址先落库；转存失败后可续下载，不再生成。
                while state.pending_urls:
                    path = None
                    for attempt in range(max(1, store_attempts)):
                        try:
                            # 已落盘的转存文件优先续用，不再下载短时效供应商地址。
                            pending = asset_store.parse_companion_asset_path(state.pending_path)
                            source = (
                                state.pending_path
                                if state.pending_path and pending and asset_store.resolve_companion_asset_path(*pending)
                                else state.pending_urls[0]
                            )
                            data, _ = await image_asset_bytes(source, max_bytes=inputs.max_image_bytes)
                            provider = state.providers[state.active_index or 0]
                            ext = await asyncio.to_thread(
                                _validate_image,
                                data,
                                size=(provider.image_size or provider.image_aspect_ratio) if scene else inputs.size,
                                size_enforced=inputs.size_enforced,
                                exact_size=scene,
                            )
                            if isinstance(inputs, CharacterImageInput) and inputs.prefer_transparent_background:
                                data = await asyncio.to_thread(prepare_transparent_image, data)
                                ext = "png"
                            state.pending_path = asset_store.image_chain_asset_path(
                                user_id,
                                state.generation_id,
                                state.active_index or 0,
                                state.pending_slots[0],
                                ext,
                                directory=state.storage_directory,
                            )
                            await _save_progress(state, save_progress)
                            path = await asset_store.save_image_chain_asset_async(
                                data,
                                user_id=user_id,
                                generation_id=state.generation_id,
                                attempt=state.active_index or 0,
                                slot=state.pending_slots[0],
                                ext=ext,
                                directory=state.storage_directory,
                            )
                            break
                        except Exception as exc:
                            if isinstance(exc, VideoProcessError):
                                raise ImageGenerationError(str(exc), internal=exc.internal) from exc
                            if isinstance(exc, ImageGenerationError) and exc.can_fallback:
                                if exc.size_mismatch:
                                    state.size_rejected += 1
                                break
                            retryable_download = isinstance(exc, httpx.TransportError) or (
                                isinstance(exc, httpx.HTTPStatusError)
                                and exc.response.status_code in {408, 429, 500, 502, 503, 504}
                            )
                            if retryable_download and attempt + 1 < max(1, store_attempts):
                                await asyncio.sleep(min(2.0, 0.25 * 2**attempt))
                                continue
                            if state.candidates:
                                state.stop_reason = "storage_failed"
                                return await _complete_images(state, save_progress)
                            raise
                    if path is None:
                        if state.pending_path:
                            await asyncio.to_thread(asset_store.unlink_companion_asset, state.pending_path)
                            state.pending_path = None
                        state.pending_urls.pop(0)
                        state.pending_slots.pop(0)
                        await _save_progress(state, save_progress)
                        continue
                    candidate = MediaCandidate(
                        path=path,
                        attempt=state.active_index or 0,
                        slot=state.pending_slots[0],
                    )
                    state.candidates.append(candidate)
                    state.pending_urls.pop(0)
                    state.pending_slots.pop(0)
                    state.pending_path = None
                    await _save_progress(state, save_progress)
                state.phase = "evaluating"
                await _save_progress(state, save_progress)
            if state.phase == "evaluating":
                for candidate in state.candidates:
                    if candidate.evaluated:
                        continue
                    data, mime = await image_asset_bytes(candidate.path)
                    uri = await asyncio.to_thread(build_data_uri, data, mime)
                    if isinstance(inputs, SceneImageInput):
                        accepted, reason = await review_scene_image(user_id, uri, before_submit=before_submit)
                        candidate.accepted = accepted
                        candidate.evaluated = True
                        candidate.result_json = json.dumps({"accepted": accepted, "reason": reason}, ensure_ascii=False)
                    else:
                        score = (
                            None
                            if state.stop_reason == "score_unavailable"
                            else await score_character_image(
                                user_id,
                                identity_uri,
                                uri,
                                identity_text=inputs.identity_text,
                                before_submit=before_submit,
                            )
                        )
                        state.accept_score(candidate, score)
                    await _save_progress(state, save_progress)
                state.phase = "ready"
                await _save_progress(state, save_progress)
            if state.stop_reason:
                return await _complete_images(state, save_progress)
            if state.remaining_slots:
                if state.active_index is None:
                    raise ImageGenerationError("图片任务缺少当前供应商")
                index = state.active_index
            else:
                state.remaining_slots = [slot for slot in range(inputs.n) if state.needs_next(slot)]
                index = state.next_index
            if not state.remaining_slots:
                return await _complete_images(state, save_progress)
            config = configs.get(index) or await resolve_frozen_media_provider(
                user_id,
                "image_gen",
                state.providers[index],
            )
            if config is None:
                state.next_index = index + 1
                state.remaining_slots = []
                await _save_progress(state, save_progress)
                continue
            if before_submit is not None:
                await before_submit()
            batch_size = state.providers[index].max_images_per_request or len(state.remaining_slots)
            slots = state.remaining_slots[:batch_size]
            state.remaining_slots = state.remaining_slots[batch_size:]
            state.begin(index)
            await _save_progress(state, save_progress)
            try:
                provider = state.providers[index]
                background = provider.background
                urls = await generate_images(
                    inputs.prompt,
                    size=provider.image_size or inputs.size,
                    n=len(slots),
                    user_id=user_id,
                    reference_image=reference_uri,
                    secondary_reference_image=secondary_uri if isinstance(inputs, CharacterImageInput) else None,
                    image_edit=inputs.image_edit if isinstance(inputs, CharacterImageInput) else False,
                    provider_config=config,
                    background=background,
                    prefer_transparent_background=isinstance(inputs, CharacterImageInput)
                    and inputs.prefer_transparent_background,
                    aspect_ratio=provider.image_aspect_ratio,
                    resolution=provider.image_resolution,
                    exact_size=provider.image_exact_size,
                )
            except ImageGenerationError as exc:
                reason, can_continue = media_failure_reason(exc)
                state.phase = "ready"
                state.remaining_slots = []
                if not can_continue:
                    state.stop_reason = reason
                await _save_progress(state, save_progress)
                if not can_continue or state.next_index == len(state.providers):
                    if state.candidates:
                        return await _complete_images(state, save_progress)
                    raise
                continue
            state.pending_urls = urls[: len(slots)]
            state.pending_slots = slots[: len(state.pending_urls)]
            state.phase = "storing"
            await _save_progress(state, save_progress)
    except BaseException:
        # 持久任务的候选由任务所有者保管；同步调用没有恢复者，必须回收。
        if save_progress is None:
            for path in state.stored_paths():
                await asyncio.to_thread(asset_store.unlink_companion_asset, path)
        raise
