"""身份保持图片：每个输出位置按冻结供应商链择优，后台调用可持久化进度。"""

import asyncio
import io
from collections.abc import Awaitable, Callable
from pathlib import Path

from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, SESSION_LOCAL, download_capped, get_file_path, get_logger
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.assets import asset_store, build_data_uri
from services.infrastructure.llm import ProviderConfig, ServiceType, resolve, resolve_reference_bytes

from .identity_review import score_character_image
from .image_generation import ImageGenerationError, generate_images, resolve_image_gen_chain
from .media_chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
)

logger = get_logger(__name__)


class CharacterImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    size: str
    n: int = Field(ge=1)
    reference_image: str
    secondary_reference_image: str | None = None
    image_edit: bool = False
    identity_reference: str
    identity_text: str = ""
    max_image_bytes: int = Field(default=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, gt=0)


class ImageChainState(MediaChainState):
    inputs: CharacterImageInput | None = None
    pending_urls: list[str] = Field(default_factory=list)
    pending_slots: list[int] = Field(default_factory=list)
    pending_path: str | None = None
    remaining_slots: list[int] = Field(default_factory=list)


ImageProgressWriter = Callable[[ImageChainState], Awaitable[None]]


def _validate_image(data: bytes) -> str:
    ext = asset_store.sniff_media_ext(data)
    if ext not in ("png", "jpg", "webp", "gif"):
        raise ImageGenerationError("供应商返回的图片无法读取", can_fallback=True)
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception as exc:
        raise ImageGenerationError("供应商返回的图片无法读取", can_fallback=True) from exc
    return ext


async def image_asset_bytes(path: str, *, max_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES) -> tuple[bytes, str]:
    parsed = asset_store.parse_companion_asset_path(path)
    if parsed is not None:
        local = asset_store.resolve_companion_asset_path(*parsed)
        if local is None:
            raise ImageGenerationError("生成图片无法读取")
        if local[0].stat().st_size > max_bytes:
            raise ImageGenerationError("生成图片超过大小限制", can_fallback=True)
        return await asyncio.to_thread(local[0].read_bytes), local[1]
    if path.startswith("temp-media/") or "/api/media/files/" in path:
        file_id = path.rsplit("/", 1)[-1].split("?", 1)[0]
        local = get_file_path(file_id)
        if local is None:
            raise ImageGenerationError("生成图片已过期")
        if Path(local[0]).stat().st_size > max_bytes:
            raise ImageGenerationError("生成图片超过大小限制", can_fallback=True)
        return await asyncio.to_thread(Path(local[0]).read_bytes), local[1]
    if path.startswith("data:"):
        try:
            data, mime = await resolve_reference_bytes(path)
        except ValueError as exc:
            raise ImageGenerationError("供应商返回的图片无法读取", can_fallback=True) from exc
    else:
        data = await download_capped(path, max_bytes=max_bytes, timeout=120.0)
        ext = asset_store.sniff_media_ext(data)
        mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}.get(
            ext,
            "image/jpeg",
        )
    if len(data) > max_bytes:
        raise ImageGenerationError("生成图片超过大小限制", can_fallback=True)
    return data, mime


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
        raise ImageGenerationError(
            "图片生成结果未知，请核对供应商任务后再决定是否重做"
            if state.stop_reason == "result_unknown"
            else "图片生成失败，未取得可用候选",
            result_unknown=state.stop_reason == "result_unknown",
        )
    state.finish(state.inputs.n)
    await _save_progress(state, writer)
    for candidate in state.candidates:
        if candidate.path not in selected:
            await asyncio.to_thread(asset_store.unlink_companion_asset, candidate.path)
    if state.pending_path and state.pending_path not in selected:
        await asyncio.to_thread(asset_store.unlink_companion_asset, state.pending_path)
    logger.info(
        "character image chain selected",
        extra={"selected": selected, "attempts": state.next_index, "stop_reason": state.stop_reason},
    )
    return selected


async def generate_character_images(
    prompt: str,
    *,
    user_id: int,
    reference_image: str,
    identity_reference: str,
    size: str = "1024x1024",
    n: int = 1,
    secondary_reference_image: str | None = None,
    image_edit: bool = False,
    identity_text: str = "",
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    store_attempts: int = 1,
    max_image_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
) -> list[str]:
    """返回已保存的用户资产；恢复只消费冻结输入、已知结果和未提交的链尾。"""
    state = state if state is not None else ImageChainState()
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
        )
        async with SESSION_LOCAL() as db:
            chain, error = await resolve_image_gen_chain(
                db,
                user_id,
                reference_image,
                image_edit=image_edit,
                multiple_references=bool(secondary_reference_image),
            )
        if error or not chain:
            raise ImageGenerationError(error or "图片生成服务未配置")
        state.providers = [FrozenMediaProvider.from_config(config) for config in chain]
        for provider in state.providers:
            provider.max_images_per_request = resolve(
                ServiceType.image_gen,
                provider.provider,
            ).max_images_per_request
        configs = dict(enumerate(chain))
        await _save_progress(state, save_progress)
    inputs = state.inputs
    if state.phase == "submitting":
        state.stop_reason = "result_unknown"
        await _save_progress(state, save_progress)
        return await _complete_images(state, save_progress)
    if state.phase == "complete":
        return await _complete_images(state, save_progress)
    try:
        while True:
            if state.phase == "storing":
                # 供应商地址先落库；转存失败后可续下载，不再生成。
                while state.pending_urls:
                    path = None
                    for attempt in range(max(1, store_attempts)):
                        try:
                            pending = asset_store.parse_companion_asset_path(state.pending_path)
                            stored = asset_store.resolve_companion_asset_path(*pending) if pending else None
                            data, _ = await image_asset_bytes(
                                state.pending_path if stored else state.pending_urls[0],
                                max_bytes=inputs.max_image_bytes,
                            )
                            ext = await asyncio.to_thread(_validate_image, data)
                            state.pending_path = asset_store.image_chain_asset_path(
                                user_id,
                                state.generation_id,
                                state.active_index or 0,
                                state.pending_slots[0],
                                ext,
                            )
                            await _save_progress(state, save_progress)
                            path = await asset_store.save_image_chain_asset_async(
                                data,
                                user_id=user_id,
                                generation_id=state.generation_id,
                                attempt=state.active_index or 0,
                                slot=state.pending_slots[0],
                                ext=ext,
                            )
                            break
                        except Exception as exc:
                            if isinstance(exc, ImageGenerationError) and exc.can_fallback:
                                break
                            if attempt + 1 < max(1, store_attempts):
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
                    score = (
                        None
                        if state.stop_reason == "score_unavailable"
                        else await score_character_image(
                            user_id,
                            inputs.identity_reference,
                            uri,
                            identity_text=inputs.identity_text,
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
            batch_size = state.providers[index].max_images_per_request or len(state.remaining_slots)
            slots = state.remaining_slots[:batch_size]
            state.remaining_slots = state.remaining_slots[batch_size:]
            state.begin(index)
            await _save_progress(state, save_progress)
            try:
                urls = await generate_images(
                    inputs.prompt,
                    size=inputs.size,
                    n=len(slots),
                    user_id=user_id,
                    reference_image=inputs.reference_image,
                    secondary_reference_image=inputs.secondary_reference_image,
                    image_edit=inputs.image_edit,
                    provider_config=config,
                    defer_storage=True,
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
            for candidate in state.candidates:
                await asyncio.to_thread(asset_store.unlink_companion_asset, candidate.path)
            if state.pending_path:
                await asyncio.to_thread(asset_store.unlink_companion_asset, state.pending_path)
        raise
