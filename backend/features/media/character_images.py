"""图片链：角色图按身份与可选画面要求择优，场景独立核查是否重复桌面伙伴。"""

import asyncio
import io
import json
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from infrastructure.ai._image_canvas import ImageCanvas, select_image_canvas
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.providers._size_aspect import ASPECT_RATIOS, SIZE_TO_ASPECT
from infrastructure.ai.providers.base import ProviderConfig, ServiceType
from infrastructure.ai.providers.registry import resolve
from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import build_data_uri
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.async_io import run_blocking
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.media_geometry import (
    MAX_DESKTOP_CROP_FRACTION,
    can_crop_to_aspect,
    resolution_fit_key,
)
from infrastructure.network import download_capped
from infrastructure.video_processing.ffmpeg import VideoProcessError
from infrastructure.video_processing.frames import prepare_action_frame
from infrastructure.video_processing.image import prepare_transparent_image
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from features.media.chain import (
    MEDIA_IDENTITY_ACCEPT_SCORE,
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
)
from features.media.identity_review import (
    ImageGoalReview,
    ImageGoalReviewFailure,
    require_image_goal_review_config,
    review_character_image_goal,
    score_character_image,
    score_character_image_with_references,
)
from features.media.image_generation import (
    ImageGenerationError,
    ImageResultDownloadError,
    ImageReviewUnavailableError,
    generate_images,
    resolve_image_gen_chain,
)
from features.media.scene_review import review_scene_image

logger = get_logger(__name__)

# 评审失败状态到链停机原因的映射；键须与 ImageGoalReviewFailure.status 一致。
_GOAL_FAILURE_STOP_REASONS = {
    "unavailable": "goal_review_unavailable",
    "invalid": "goal_review_invalid",
    "blocked": "goal_review_blocked",
}

# 2% 容差覆盖 local 1792x1024 与 16:9 的差异，并拒绝 5:3。
_SIZE_ASPECT_TOLERANCE = 0.02


class CharacterImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["character"] = "character"
    prompt: str
    size: str
    aspect_ratio: str | None = None
    n: int = Field(ge=1)
    reference_image: str
    secondary_reference_image: str | None = None
    environment_reference_image: str | None = None
    image_edit: bool = False
    identity_reference: str
    identity_secondary_reference: str | None = None
    review_environment_reference_image: str | None = None
    identity_reference_mode: Literal["primary", "portrait_and_outfit"] = "portrait_and_outfit"
    goal_review: Literal["none", "required"] = "none"
    identity_text: str = ""
    max_image_bytes: int = Field(default=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, gt=0)
    size_enforced: bool = False
    prefer_transparent_background: bool = False
    prepare_action_frame: bool = False


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
        ("environment_reference_image", "environment"),
        ("identity_reference", "identity"),
        ("identity_secondary_reference", "identity2"),
        ("review_environment_reference_image", "review_environment"),
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
    goal_reviews: dict[str, ImageGoalReview] = Field(default_factory=dict)
    goal_review_failures: dict[str, ImageGoalReviewFailure] = Field(default_factory=dict)

    def resume_download(self) -> None:
        """仅接收已返回的图片并完成检查，不再生成候选。"""
        self.next_index = len(self.providers)
        self.remaining_slots = []

    def _goal_enabled(self) -> bool:
        return isinstance(self.inputs, CharacterImageInput) and self.inputs.goal_review == "required"

    def _needs_goal_review(self, candidate: MediaCandidate) -> bool:
        """续审只补缺失决定：尚无结论、仅有失败记录，或旧结论缺自然度判定的候选仍需检查。"""
        goal = self.goal_reviews.get(candidate.path)
        if goal is not None:
            return goal.verdict == "pass" and goal.quality_verdict is None
        return candidate.evaluated or candidate.path in self.goal_review_failures

    def can_retry_goal_review(self) -> bool:
        return (
            self._goal_enabled()
            and self.phase in {"evaluating", "complete"}
            and any(self._needs_goal_review(candidate) for candidate in self.candidates)
            and self.stop_reason
            in {
                "accepted",
                "exhausted",
                "goal_review_unavailable",
                "goal_review_invalid",
                "score_unavailable",
                "",
            }
        )

    def _passes_review(self, candidate: MediaCandidate | None) -> bool:
        if candidate is None or candidate.score is None or candidate.score < MEDIA_IDENTITY_ACCEPT_SCORE:
            return False
        goal = self.goal_reviews.get(candidate.path)
        return goal is not None and goal.verdict == "pass" and goal.quality_verdict == "pass"

    def selected_origin(self, slot: int = 0) -> GenerationOrigin | None:
        candidate = self.best(slot)
        if candidate is None or not 0 <= candidate.attempt < len(self.providers):
            return None
        return self.providers[candidate.attempt].generation_origin(ServiceType.image_gen)

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
        if self._goal_enabled():
            return max(
                (
                    candidate
                    for candidate in self.candidates
                    if candidate.slot == slot and self._passes_review(candidate)
                ),
                key=lambda candidate: candidate.score if candidate.score is not None else -1,
                default=None,
            )
        return super().best(slot)

    def needs_next(self, slot: int = 0) -> bool:
        if isinstance(self.inputs, SceneImageInput):
            return not self.stop_reason and self.next_index < len(self.providers) and self.best(slot) is None
        if self._goal_enabled():
            return (
                not self.stop_reason
                and self.next_index < len(self.providers)
                and not self._passes_review(self.best(slot))
            )
        return super().needs_next(slot)

    def finish(self, slots: int = 1) -> None:
        if isinstance(self.inputs, SceneImageInput):
            if not self.stop_reason:
                self.stop_reason = (
                    "accepted" if all(self.best(slot) is not None for slot in range(slots)) else "exhausted"
                )
            self.phase = "complete"
        elif self._goal_enabled():
            if not self.stop_reason:
                self.stop_reason = (
                    "accepted" if all(self._passes_review(self.best(slot)) for slot in range(slots)) else "exhausted"
                )
            self.phase = "complete"
        else:
            super().finish(slots)

    def generated_paths(self) -> set[str]:
        """本次制作拥有的候选、转存与处理产物，不包含调用方的参考素材。"""
        return {
            self.pending_path or "",
            *(candidate.path for candidate in self.candidates),
            *(path for candidate in self.candidates for path in candidate.artifacts),
        } - {""}

    def stored_paths(self) -> set[str]:
        """已落盘产物及冻结输入依赖，供业务所有者保护与回收。"""
        fields = _REFERENCE_FIELDS.get(type(self.inputs), ()) if self.inputs is not None else ()
        frozen = [getattr(self.inputs, attr) for attr, _label in fields]
        return self.generated_paths() | {
            path for path in frozen if path and parse_companion_asset_path(path) is not None
        }


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


def _size_matches_request(
    size: str,
    width: int,
    height: int,
    *,
    exact_size: bool = False,
    tolerance: float = _SIZE_ASPECT_TOLERANCE,
) -> bool:
    expected = _expected_aspect_ratio(size, exact_size=exact_size)
    if expected is None or width <= 0 or height <= 0:
        return True
    actual = width / height
    return abs(actual - expected) <= expected * tolerance


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
    native_mismatch = size_enforced and size and not _size_matches_request(size, width, height, exact_size=exact_size)
    if native_mismatch:
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
    parsed = parse_companion_asset_path(path)
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
        data = await download_capped(path, max_bytes=max_bytes)
        mime = asset_store.image_mime_for_extension(asset_store.sniff_media_ext(data) or "") or "image/jpeg"
    if len(data) > max_bytes:
        raise ImageGenerationError("生成图片超过大小限制", can_fallback=True)
    return data, mime


def _reference_image_canvas(data: bytes) -> ImageCanvas:
    normalized, _mime = asset_store.validate_image_bytes(data)
    with Image.open(io.BytesIO(normalized)) as image:
        ImageOps.exif_transpose(image, in_place=True)
        width, height = image.size
    return ImageCanvas(size=f"{width}x{height}", exact_size=True, native_width=width, native_height=height)


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
        if not value or value in frozen or parse_companion_asset_path(value) is not None:
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


async def _complete_images(state: ImageChainState, writer: ImageProgressWriter | None) -> list[str]:
    if state.inputs is None:
        raise ImageGenerationError("图片任务缺少生成输入")
    selected = [candidate.path for slot in range(state.inputs.n) if (candidate := state.best(slot)) is not None]
    if state.can_retry_goal_review() and (writer is not None or not selected):
        state.phase = "evaluating"
        await _save_progress(state, writer)
        if selected:
            return selected
        message = (
            "图片已生成，画面检查暂未完成，请稍后继续检查已有图片"
            if writer is not None
            else "图片已生成，但画面检查暂不可用，本次结果未交付"
        )
        raise ImageReviewUnavailableError(message)
    if not selected:
        blocked = state.stop_reason == "goal_review_blocked"
        if blocked or (
            isinstance(state.inputs, CharacterImageInput)
            and state.inputs.goal_review == "required"
            and state.candidates
        ):
            state.finish(state.inputs.n)
            await _save_progress(state, writer)
            if state.stop_reason != "result_unknown":
                raise ImageGenerationError(
                    "图片检查未通过服务审核，请调整制作要求或参考形象后重试"
                    if blocked
                    else "图片未通过身份与画面要求检查，本次结果未交付"
                )
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
    retained = set(selected) | {
        path for candidate in state.candidates if candidate.path in selected for path in candidate.artifacts
    }
    obsolete = state.generated_paths() - retained
    state.finish(state.inputs.n)
    await _save_progress(state, writer)
    for path in obsolete:
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
    task: AiTaskKey,
    storage_directory: str,
    reference_image: str,
    identity_reference: str,
    size: str = "1024x1024",
    aspect_ratio: str | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
    n: int = 1,
    secondary_reference_image: str | None = None,
    environment_reference_image: str | None = None,
    image_edit: bool = False,
    identity_text: str = "",
    identity_secondary_reference: str | None = None,
    review_environment_reference_image: str | None = None,
    identity_reference_mode: Literal["primary", "portrait_and_outfit"] = "portrait_and_outfit",
    goal_review: Literal["none", "required"] = "none",
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    store_attempts: int = 3,
    before_submit: Callable[[], Awaitable[None]] | None = None,
    max_image_bytes: int = REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
    size_enforced: bool = False,
    prefer_transparent_background: bool = False,
    prepare_action_frame: bool = False,
    selection: ProviderSelection | None = None,
    nearest_resolution: bool = False,
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
        if (target_width is None) != (target_height is None):
            raise ValueError("图片画布须同时提供目标宽高")
        if target_width is not None and target_height is not None and min(target_width, target_height) <= 0:
            raise ValueError("图片目标尺寸必须为正整数")
        if nearest_resolution and (target_width is None or target_height is None):
            raise ValueError("最接近分辨率的图片规划需要目标宽高")
        if image_edit and (secondary_reference_image or environment_reference_image):
            raise ImageGenerationError("图像编辑不支持附加参考图，请改用重新生成")
        if goal_review == "required":
            if not reference_image or not identity_reference:
                raise ImageGenerationError("画面要求检查需要生成参考及冻结身份参考")
            try:
                await require_image_goal_review_config(user_id, task)
            except MissingLlmConfigError as exc:
                raise ImageGenerationError(str(exc)) from exc
        state.inputs = CharacterImageInput(
            prompt=prompt,
            size=size,
            aspect_ratio=aspect_ratio,
            n=n,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            environment_reference_image=environment_reference_image,
            image_edit=image_edit,
            identity_reference=identity_reference,
            identity_secondary_reference=identity_secondary_reference,
            review_environment_reference_image=review_environment_reference_image,
            identity_reference_mode=identity_reference_mode,
            goal_review=goal_review,
            identity_text=identity_text,
            max_image_bytes=max_image_bytes,
            size_enforced=size_enforced,
            prefer_transparent_background=prefer_transparent_background,
            prepare_action_frame=prepare_action_frame,
        )
        async with SESSION_LOCAL() as db:
            plan = await resolve_image_gen_chain(
                db,
                user_id,
                task=task,
                has_reference=bool(reference_image),
                image_edit=image_edit,
                multiple_references=bool(secondary_reference_image or environment_reference_image),
                environment_reference=bool(environment_reference_image),
                prompt_chars=len(prompt),
                selection=selection,
            )
        chain, error = plan.providers, plan.error
        if error or not chain:
            raise ImageGenerationError(error or "图片生成服务未配置")
        rank_by_resolution = nearest_resolution and not plan.preserve_order
        local_edit_canvas: ImageCanvas | None = None
        planned: list[tuple[ProviderConfig, FrozenMediaProvider, tuple[float, int, float]]] = []
        for config in chain:
            provider = FrozenMediaProvider.from_config(config)
            provider_cls = resolve(ServiceType.image_gen, provider.provider)
            provider.max_images_per_request = provider_cls.max_images_per_request
            if prefer_transparent_background and provider_cls.supports_transparent_background:
                provider.background = "transparent"
            fit_key = (0.0, 0, 0.0)
            if target_width is not None and target_height is not None:
                try:
                    if nearest_resolution and image_edit and config.provider_name == "local":
                        if local_edit_canvas is None:
                            reference_data, _mime = await image_asset_bytes(reference_image, max_bytes=max_image_bytes)
                            local_edit_canvas = await asyncio.to_thread(_reference_image_canvas, reference_data)
                        canvas = local_edit_canvas
                    else:
                        canvas = select_image_canvas(config, target_width, target_height, nearest=nearest_resolution)
                except ValueError:
                    if nearest_resolution:
                        continue
                    raise
                if "x" in canvas.size.lower():
                    native_width, native_height = (int(value) for value in canvas.size.lower().split("x"))
                    can_fit = can_crop_to_aspect(native_width, native_height, target_width, target_height)
                else:
                    native_aspect = _expected_aspect_ratio(canvas.aspect_ratio or canvas.size)
                    target_aspect = target_width / target_height
                    can_fit = (
                        native_aspect is not None
                        and 1
                        - min(
                            native_aspect / target_aspect,
                            target_aspect / native_aspect,
                        )
                        <= MAX_DESKTOP_CROP_FRACTION + 1e-9
                    )
                if not can_fit:
                    continue
                if nearest_resolution:
                    if canvas.native_width is None or canvas.native_height is None:
                        raise ValueError("图片档位缺少原生尺寸规格")
                    fit_key = resolution_fit_key(
                        canvas.native_width,
                        canvas.native_height,
                        target_width,
                        target_height,
                    )
                provider.image_size = canvas.size
                provider.image_aspect_ratio = canvas.aspect_ratio
                provider.image_resolution = canvas.resolution
                provider.image_exact_size = canvas.exact_size
            planned.append((config, provider, fit_key))
        if not planned:
            raise ImageGenerationError("当前图片生成供应商没有可在少量裁边后适配目标的原生画幅")
        if rank_by_resolution:
            planned.sort(key=lambda item: item[2])
        chain = [config for config, _provider, _key in planned]
        state.providers = [provider for _config, provider, _key in planned]
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
    if state.inputs.identity_reference_mode != identity_reference_mode:
        raise ImageGenerationError("图片任务的身份参考职责已变化")
    return await _run_image_chain(
        state,
        user_id=user_id,
        task=task,
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
    selection: ProviderSelection | None = None,
    task: AiTaskKey = AiTaskKey.scene,
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
            resolved = await resolve_image_gen_chain(
                db,
                user_id,
                task=task,
                has_reference=bool(reference_image),
                prompt_chars=len(prompt),
                environment_reference=True,
                selection=selection,
            )
        chain, error = resolved.providers, resolved.error
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
        task=task,
        configs=configs,
        save_progress=save_progress,
        store_attempts=store_attempts,
        before_submit=before_submit,
    )


async def _run_image_chain(
    state: ImageChainState,
    *,
    user_id: int,
    task: AiTaskKey,
    configs: dict[int, ProviderConfig],
    save_progress: ImageProgressWriter | None,
    store_attempts: int,
    before_submit: Callable[[], Awaitable[None]] | None,
) -> list[str]:
    inputs = state.inputs
    if inputs is None:
        raise ImageGenerationError("图片任务缺少生成输入")
    scene = isinstance(inputs, SceneImageInput)
    review_only = state.can_retry_goal_review()
    if review_only:
        state.phase = "evaluating"
        state.stop_reason = ""
        for candidate in state.candidates:
            goal = state.goal_reviews.get(candidate.path)
            if goal is not None and goal.verdict == "pass" and goal.quality_verdict is None:
                state.goal_review_failures[candidate.path] = ImageGoalReviewFailure(
                    status="invalid",
                    score=candidate.score,
                    verdict=goal.verdict,
                )
                state.goal_reviews.pop(candidate.path)
            elif goal is None and candidate.evaluated and candidate.path not in state.goal_review_failures:
                state.goal_review_failures[candidate.path] = ImageGoalReviewFailure(
                    status="invalid",
                    score=candidate.score,
                )
            if candidate.path in state.goal_review_failures and candidate.path not in state.goal_reviews:
                candidate.evaluated = False
    if state.phase == "submitting":
        state.stop_reason = "result_unknown"
        await _save_progress(state, save_progress)
        return await _complete_images(state, save_progress)
    if state.phase == "complete":
        return await _complete_images(state, save_progress)
    # 参考图在链运行内不变：物化一次供提交与评分复用，避免逐候选重复读盘/解码。
    asset_memo: dict[str, tuple[bytes, str]] = {}
    uri_memo: dict[str, str] = {}

    async def _load_asset(value: str) -> tuple[bytes, str]:
        hit = asset_memo.get(value)
        if hit is None:
            hit = await image_asset_bytes(value, max_bytes=inputs.max_image_bytes)
            asset_memo[value] = hit
        return hit

    async def _materialize(value: str | None) -> str | None:
        if not value or value.startswith("data:"):
            return value
        hit = uri_memo.get(value)
        if hit is None:
            data, mime = await _load_asset(value)
            hit = await asyncio.to_thread(build_data_uri, data, mime)
            uri_memo[value] = hit
        return hit

    reference_uri = await _materialize(inputs.reference_image)
    secondary_uri = (
        await _materialize(inputs.secondary_reference_image) if isinstance(inputs, CharacterImageInput) else None
    )
    identity_uri = await _materialize(inputs.identity_reference) if isinstance(inputs, CharacterImageInput) else None
    identity_secondary_uri = (
        await _materialize(inputs.identity_secondary_reference or inputs.secondary_reference_image)
        if isinstance(inputs, CharacterImageInput)
        else None
    )
    environment_uri = (
        await _materialize(inputs.environment_reference_image) if isinstance(inputs, CharacterImageInput) else None
    )
    review_environment_uri = (
        await _materialize(inputs.review_environment_reference_image or inputs.environment_reference_image)
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
                            pending = parse_companion_asset_path(state.pending_path)
                            source = (
                                state.pending_path
                                if state.pending_path and pending and asset_store.resolve_companion_asset_path(*pending)
                                else state.pending_urls[0]
                            )
                            stored_pending = source == state.pending_path
                            data, _ = await image_asset_bytes(source, max_bytes=inputs.max_image_bytes)
                            provider = state.providers[state.active_index or 0]
                            ext = await asyncio.to_thread(
                                _validate_image,
                                data,
                                size=provider.image_size or provider.image_aspect_ratio or inputs.size,
                                size_enforced=inputs.size_enforced and not stored_pending,
                                exact_size=scene or provider.image_exact_size,
                            )
                            if isinstance(inputs, CharacterImageInput) and inputs.prefer_transparent_background:
                                data = await asyncio.to_thread(prepare_transparent_image, data)
                                ext = "png"
                            if isinstance(inputs, CharacterImageInput) and inputs.prepare_action_frame:
                                data = await run_blocking(prepare_action_frame, data)
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
                            if isinstance(exc, httpx.TransportError | httpx.HTTPStatusError):
                                raise ImageResultDownloadError(
                                    "图片已生成，但成品下载未完成；保留原结果等待继续接收",
                                    internal=f"{type(exc).__name__}: {exc}",
                                ) from exc
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
                        if state._goal_enabled() and candidate.path not in state.goal_reviews and not state.stop_reason:
                            state.stop_reason = "goal_review_invalid"
                        continue
                    data, mime = await image_asset_bytes(candidate.path)
                    uri = await asyncio.to_thread(build_data_uri, data, mime)
                    if isinstance(inputs, SceneImageInput):
                        accepted, reason = await review_scene_image(
                            user_id, uri, before_submit=before_submit, task=task
                        )
                        candidate.accepted = accepted
                        candidate.evaluated = True
                        candidate.result_json = json.dumps({"accepted": accepted, "reason": reason}, ensure_ascii=False)
                    else:
                        if state.stop_reason in ("score_unavailable", "goal_review_invalid"):
                            score = None
                        elif inputs.goal_review == "required":
                            review = await review_character_image_goal(
                                user_id,
                                identity_uri or "",
                                uri,
                                secondary_uri=identity_secondary_uri,
                                environment_uri=review_environment_uri,
                                generation_prompt=inputs.prompt,
                                identity_reference_mode=inputs.identity_reference_mode,
                                identity_text=inputs.identity_text,
                                prepared_action_frame=inputs.prepare_action_frame,
                                previous_failure=state.goal_review_failures.get(candidate.path),
                                task=task,
                                before_submit=before_submit,
                            )
                            score = review.score
                            if review.goal is not None:
                                state.goal_reviews[candidate.path] = review.goal
                                state.goal_review_failures.pop(candidate.path, None)
                            else:
                                failure = review.failure or ImageGoalReviewFailure(status="invalid", score=score)
                                state.goal_review_failures[candidate.path] = failure
                                candidate.score = score
                                state.stop_reason = _GOAL_FAILURE_STOP_REASONS[failure.status]
                                await _save_progress(state, save_progress)
                                return await _complete_images(state, save_progress)
                        elif secondary_uri and inputs.identity_reference_mode == "portrait_and_outfit":
                            score = await score_character_image_with_references(
                                user_id,
                                (identity_uri or "", secondary_uri),
                                uri,
                                task=task,
                                identity_text=inputs.identity_text,
                                before_submit=before_submit,
                            )
                        else:
                            score = await score_character_image(
                                user_id,
                                identity_uri,
                                uri,
                                task=task,
                                identity_text=inputs.identity_text,
                                before_submit=before_submit,
                            )
                        state.accept_score(candidate, score)
                    await _save_progress(state, save_progress)
                state.phase = "ready"
                await _save_progress(state, save_progress)
                if review_only:
                    return await _complete_images(state, save_progress)
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
            state.active_index = index
            state.remaining_slots = state.remaining_slots[batch_size:]
            state.begin(index)
            await _save_progress(state, save_progress)
            try:
                provider = state.providers[index]
                background = provider.background
                result = await generate_images(
                    inputs.prompt,
                    size=provider.image_size or inputs.size,
                    n=len(slots),
                    user_id=user_id,
                    task=task,
                    reference_image=reference_uri,
                    secondary_reference_image=secondary_uri if isinstance(inputs, CharacterImageInput) else None,
                    environment_reference_image=environment_uri,
                    image_edit=inputs.image_edit if isinstance(inputs, CharacterImageInput) else False,
                    provider_config=config,
                    background=background,
                    prefer_transparent_background=isinstance(inputs, CharacterImageInput)
                    and inputs.prefer_transparent_background,
                    aspect_ratio=provider.image_aspect_ratio
                    or (inputs.aspect_ratio if isinstance(inputs, CharacterImageInput) else None),
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
            state.pending_urls = result.urls[: len(slots)]
            state.pending_slots = slots[: len(state.pending_urls)]
            state.phase = "storing"
            await _save_progress(state, save_progress)
    except BaseException:
        # 持久任务的候选由任务所有者保管；同步调用没有恢复者，必须回收。
        if save_progress is None:
            for path in state.generated_paths():
                await asyncio.to_thread(asset_store.unlink_companion_asset, path)
        raise
