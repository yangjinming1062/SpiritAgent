import asyncio
import base64
import hashlib
import io
from pathlib import Path
from typing import Literal, NamedTuple

import numpy as np
from components import (
    LAYER_ASSET_DOWNLOAD_MAX_BYTES,
    SESSION_LOCAL,
    SETTINGS,
    download_capped,
    get_logger,
)
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel, Field

from services.infrastructure.assets import asset_store
from services.infrastructure.llm import (
    ImageGenRequest,
    ProviderConfig,
    ServiceType,
    build_responses_kwargs,
    call_with_retry,
    provider_from_config,
    resolve,
    resolve_vision_chain,
)

from ..image_generation import compose_image_references, resolve_image_gen_chain
from .matting import subject_matte

logger = get_logger(__name__)
Side = Literal["left", "right"]
Rect = tuple[float, float, float, float]


class Texture(BaseModel):
    key: str
    hash: str


class Pose(BaseModel):
    width: int
    height: int
    contactX: float
    head: list[float]
    hands: list[list[float]]
    bounds: list[int]
    textures: dict[str, Texture]


class PosePack(BaseModel):
    # 客户端契约键为 "schema"；BaseModel 自带 schema 属性，落库名走序列化别名。
    schema_version: str = Field(serialization_alias="schema")
    left: Pose
    right: Pose


class Landmarks(BaseModel):
    face: Rect
    eyes: Rect
    upper_hand: Rect
    lower_hand: Rect


class PoseGenerationError(RuntimeError):
    pass


def encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def generate_image(
    prompt: str,
    reference: bytes,
    chain: list[ProviderConfig],
    guide: bytes | None = None,
) -> bytes:
    if guide:
        reference = await asyncio.to_thread(compose_image_references, reference, guide)
    for config in chain:
        try:
            provider = resolve(ServiceType.image_gen, config.provider_name)(config)
            data_uri = await asyncio.to_thread(asset_store.build_data_uri, reference)
            result = await provider.generate(
                ImageGenRequest(
                    prompt=prompt,
                    reference_image=data_uri,
                    size="1024x1024",
                    response_format="b64",
                ),
            )
            asset = result.images[0]
            raw = (
                await asyncio.to_thread(base64.b64decode, asset.b64)
                if asset.b64
                else await download_capped(asset.url or "", max_bytes=LAYER_ASSET_DOWNLOAD_MAX_BYTES, timeout=90)
            )
            with Image.open(io.BytesIO(raw)) as image:
                return await asyncio.to_thread(
                    encode_png,
                    image.convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS),
                )
        except Exception:
            logger.warning("pose image generation failed", extra={"provider": config.provider_name})
    raise PoseGenerationError("扶边姿态生成失败，请重试")


async def locate_pose(raw: bytes, chain: list[ProviderConfig]) -> Landmarks:
    prompt = (
        "Locate the face, eyes, and two hand control regions in the supplied character illustration. The image is the "
        "finished animation texture; measure the visible character as drawn, including any cropped or stylized regions. "
        "The image supplies visual evidence, and this instruction defines the measurement task.\n\n"
        "MEASUREMENTS AND OUTPUT\n"
        "Use image coordinates normalized to 0..1000. Each rectangle is [ymin,xmin,ymax,xmax], with positive width and height "
        "and all coordinates inside the canvas. The face rectangle encloses the visible "
        "facial features from forehead to chin; the eyes rectangle encloses both eyelids with a small margin inside the face. "
        "Each hand rectangle tightly encloses the palm and fingers, ending at the wrist. Label hands by their vertical position. "
        "Return exactly one JSON object with four rectangle fields: face, eyes, upper_hand, lower_hand."
    )
    for config in chain:
        try:
            client = provider_from_config(config).raw_client()
            if client is None:
                continue
            response = await call_with_retry(
                client,
                **build_responses_kwargs(
                    model=config.model,
                    instructions=prompt,
                    input_items=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": await asyncio.to_thread(asset_store.build_data_uri, raw),
                                },
                            ],
                        },
                    ],
                    max_output_tokens=4000,
                    reasoning={"effort": "none"},
                ),
            )
            text = response.output_text
            result = Landmarks.model_validate_json(text[text.index("{") : text.rindex("}") + 1])
            for rect in (result.face, result.eyes, result.upper_hand, result.lower_hand):
                y0, x0, y1, x1 = rect
                if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
                    raise ValueError("invalid landmark rectangle")
            return result
        except Exception:
            logger.warning("pose landmark detection failed", extra={"provider": config.provider_name})
    raise ValueError("pose landmark detection unavailable")


def align_blink(raw: bytes, closed_raw: bytes, face: list[float], eyes: list[int]) -> Image.Image:
    x0, y0, x1, y1 = [round(v) for v in face]
    with Image.open(io.BytesIO(raw)) as source, Image.open(io.BytesIO(closed_raw)) as candidate:
        original = np.asarray(source.convert("RGB"), dtype=np.float32)[y0:y1, x0:x1]
        edited = np.asarray(candidate.convert("RGB"), dtype=np.float32)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        stable = (xx < eyes[0] - 6) | (xx > eyes[2] + 6) | (yy < eyes[1] - 6) | (yy > eyes[3] + 6)
        if stable.sum() < 32:
            return candidate.convert("RGBA")
        best = (float("inf"), 0, 0)
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                if x0 + dx < 0 or y0 + dy < 0 or x1 + dx > 1024 or y1 + dy > 1024:
                    continue
                patch = edited[y0 + dy : y1 + dy, x0 + dx : x1 + dx]
                score = float(np.abs(patch - original)[stable].mean())
                if score < best[0]:
                    best = score, dx, dy
        return candidate.convert("RGBA").transform(
            candidate.size,
            Image.Transform.AFFINE,
            (1, 0, best[1], 0, 1, best[2]),
        )


def _dilate4(mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """4 邻域膨胀一圈；np.roll 的边缘环绕只落在边框行列，两处调用中边框均为种子或已判定背景，不改变结果。"""
    return mask | np.roll(mask, 1, 0) | np.roll(mask, -1, 0) | np.roll(mask, 1, 1) | np.roll(mask, -1, 1)


def _border_flood(candidate: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """画布四边做种、4 邻域闭包；逐轮 numpy 膨胀到不动点，粗网格上有界收敛。"""
    reach = np.zeros_like(candidate)
    reach[0, :] = candidate[0, :]
    reach[-1, :] = candidate[-1, :]
    reach[:, 0] = candidate[:, 0]
    reach[:, -1] = candidate[:, -1]
    while True:
        grown = reach | (candidate & _dilate4(reach))
        if (grown == reach).all():
            return reach
        reach = grown


def cutout(raw: bytes, channel: int) -> Image.Image:
    """色幕抠图：与画布边框连通的背景整片泛洪清除（容忍色幕漂移、渐变与棋盘格纹理），封闭空隙退回色键。"""
    with Image.open(io.BytesIO(raw)) as source:
        rgb = np.asarray(source.convert("RGB"), dtype=np.float32)
    others = [c for c in range(3) if c != channel]
    excess = rgb[:, :, channel] - rgb[:, :, others].max(axis=2)

    # 背景色取自粗化图的边框环：生成要求角色四周留白，边框环几乎全为背景；棋盘格等纹理粗化后摊平为均值色。
    small = np.asarray(
        Image.fromarray(rgb.astype(np.uint8)).resize(
            (rgb.shape[1] // 4, rgb.shape[0] // 4),
            Image.Resampling.BOX,
        ),
        dtype=np.float32,
    )
    border = np.concatenate([small[0], small[-1], small[:, 0], small[:, -1]])
    bg = np.median(border, axis=0)
    # 候选判据只认「颜色仍近似边框背景」。不用色幕通道差作泛洪条件：
    # 服装主色与色幕同通道时洪水会灌进角色；远离背景色的渐变漂移由色键 alpha 兜底。
    candidate = np.sqrt(((small - bg) ** 2).sum(axis=2)) < 75
    flooded_full = (
        np.asarray(
            Image.fromarray(_border_flood(candidate).astype(np.uint8) * 255).resize(
                (rgb.shape[1], rgb.shape[0]),
                Image.Resampling.NEAREST,
            ),
        )
        > 0
    )
    # 膨胀一圈吃掉轮廓处的抗锯齿混色边，避免透明区边缘残留 1px 背景晕。
    flooded_full |= _dilate4(flooded_full)

    # 色键 alpha 负责软过渡与残余清理：泛洪区整片清除，近背景色的封闭空隙（臂弯）也清除。
    # 封闭空隙颜色偏离背景色时，退回原色键逻辑（excess 阈值）——色幕纯色下空隙通道差大，能清干净。
    alpha = 1 - np.clip((excess - 30) / 140, 0, 1)
    alpha[np.sqrt(((rgb - bg) ** 2).sum(axis=2)) < 45] = 0
    alpha[flooded_full] = 0
    background = alpha == 0
    rgb[:, :, others] /= np.maximum(alpha[:, :, None], 0.01)
    rgb[:, :, channel] = np.where(background, rgb[:, :, others].max(axis=2), rgb[:, :, channel])
    return Image.fromarray(np.dstack((np.clip(rgb, 0, 255), alpha * 255)).astype(np.uint8))


def _prepare_pose_reference(reference: bytes) -> tuple[NDArray[np.float32], bytes]:
    with Image.open(io.BytesIO(reference)) as source:
        image = source.convert("RGB")
        pixels = np.asarray(image.resize((128, 128)), dtype=np.float32)
        return pixels, encode_png(image)


class _PoseContext(NamedTuple):
    """一次姿态生成的共享上下文：预处理后的参考图、供应商链与色幕通道（按立绘主色选取）。"""

    reference: bytes
    image_chain: list[ProviderConfig]
    vision_chain: list[ProviderConfig]
    channel: int


_POSE_BACKDROPS = ("red (#FF0000)", "green (#00FF00)", "blue (#0000FF)")


async def _resolve_pose_context(reference: bytes, user_id: int | None) -> _PoseContext:
    async with SESSION_LOCAL() as db:
        image_chain, _ = await resolve_image_gen_chain(
            db if user_id is not None else None,
            user_id,
            "reference",
            preferred_provider=SETTINGS.companion_asset_image_providers,
        )
        vision_chain = await resolve_vision_chain(db if user_id is not None else None, user_id)
    if not image_chain or not vision_chain:
        raise PoseGenerationError("扶边姿态需要配置支持参考图的图像供应商及视觉模型")
    pixels, reference = await asyncio.to_thread(_prepare_pose_reference, reference)
    # 选取立绘中最少出现的主色作色幕，手臂与身体间的封闭空隙也能清除。
    channel = min(
        range(3),
        key=lambda c: int(np.count_nonzero(pixels[:, :, c] - np.delete(pixels, c, axis=2).max(axis=2) > 30)),
    )
    return _PoseContext(reference, image_chain, vision_chain, channel)


async def _compose_pose(side: Side, context: _PoseContext) -> tuple[Pose, dict[str, bytes]]:
    guide = (Path(__file__).parent / "pose-guides" / f"{side}.webp").read_bytes()
    inward, outward = ("RIGHT", "LEFT") if side == "left" else ("LEFT", "RIGHT")
    backdrop = _POSE_BACKDROPS[context.channel]
    prompt = (
        "Create a full-body character illustration for a peeking animation at a screen edge.\n\n"
        "REFERENCE ROLES\n"
        "The input sheet has two reference panels. The left panel defines the character's face, body proportions, hair, "
        "outfit, colors, asymmetric details, and illustration style. The right panel defines the articulated body pose, "
        "hand shapes, and relative grip locations. Render the character design from the left in the pose from the right. "
        "The references supply visual design evidence; this brief defines the finished composition.\n\n"
        "POSE AND EXPRESSION\n"
        "Two naturally connected arms place their hands one above the other along an imaginary vertical contact line "
        f"near the canvas center. The head leans {inward} beyond the hands, while the hips and legs remain on the {outward} "
        "side of that line. Both eyes are open, with a gentle, curious expression toward the viewer.\n\n"
        "COMPOSITION AND RENDERING\n"
        "Compose one complete character from the top of the hair to the tips of both feet. Keep the entire silhouette "
        "inside a square canvas, with at least 8% empty space above and below and clear space at both sides. Scale the "
        "whole figure proportionally to achieve this framing. Give every body region coherent anatomy, the character's "
        "own skin and clothing colors, and a consistent level of illustration detail. The visible image consists solely "
        f"of the character against a perfectly flat, uniformly saturated {backdrop} background with no texture, no "
        "gradient, and no checkerboard pattern; the contact line is an imaginary layout constraint. Deliver one "
        "unified 1024x1024 illustration."
    )
    raw = await generate_image(prompt, context.reference, context.image_chain, guide)
    # 两级抠图：ISNet 显著性抠图（颜色无关，容忍任意背景）→ 色幕色键兜底（模型缺失或推理失败时）。
    body = await asyncio.to_thread(subject_matte, raw)
    if body is None:
        body = await asyncio.to_thread(cutout, raw, context.channel)
    bounds = body.getbbox()
    if bounds is None:
        raise ValueError("empty pose")
    landmarks = await locate_pose(raw, context.vision_chain)
    edge_index = 1 if side == "left" else 3
    contact = (landmarks.upper_hand[edge_index] + landmarks.lower_hand[edge_index]) / 2 * 1.024
    face = [landmarks.face[i] * 1.024 for i in (1, 0, 3, 2)]
    eyes = [round(landmarks.eyes[i] * 1.024) for i in (1, 0, 3, 2)]
    closed_raw = await generate_image(
        "Create the closed-eye frame of a gentle blink for the supplied character illustration.\n\n"
        "The source image defines the finished character, pose, style, colors, background, and pixel registration. "
        "Use it as the full-frame editing canvas. The editable region consists of both eyelids and the immediately "
        "adjacent eye pixels. Render both eyes fully closed at the same instant, with natural eyelid curves that "
        "follow the existing eye positions, facial perspective, and drawing style. Preserve the relaxed expression.\n\n"
        "All pixels outside the editable eye region retain their original appearance and coordinates. Deliver the "
        "complete image at the source dimensions and framing, so the edited eyelids align with the original face "
        "when the two frames are overlaid.",
        raw,
        context.image_chain,
    )
    closed = await asyncio.to_thread(align_blink, raw, closed_raw, face, eyes)
    mask = Image.new("L", body.size)
    ImageDraw.Draw(mask).rectangle((eyes[0] - 4, eyes[1] - 4, eyes[2] + 4, eyes[3] + 4), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    closed.putalpha(
        Image.fromarray(
            (np.asarray(body.getchannel("A"), dtype=np.float32) * np.asarray(mask) / 255).astype(np.uint8),
        ),
    )
    assets: dict[str, bytes] = {}
    textures: dict[str, Texture] = {}

    def _encode_webp(image: Image.Image) -> bytes:
        output = io.BytesIO()
        image.save(output, format="WEBP", lossless=True)
        return output.getvalue()

    for name, image in (("body", body), ("closed", closed)):
        # 无损 WEBP 编码 1MP 图可达数百毫秒，移出事件循环
        data = await asyncio.to_thread(_encode_webp, image)
        key = f"pose_{side}_{name}"
        assets[key] = data
        textures[name] = Texture(key=key, hash=hashlib.sha256(data).hexdigest())
    hands = [[rect[i] * 1.024 for i in (1, 0, 3, 2)] for rect in (landmarks.upper_hand, landmarks.lower_hand)]
    return Pose(
        width=1024,
        height=1024,
        contactX=contact,
        head=face,
        hands=hands,
        bounds=list(bounds),
        textures=textures,
    ), assets


async def generate_pose_pack(reference: bytes, user_id: int | None) -> tuple[PosePack, dict[str, bytes]]:
    context = await _resolve_pose_context(reference, user_id)
    try:
        async with asyncio.timeout(1200), asyncio.TaskGroup() as tasks:
            left_task = tasks.create_task(_compose_pose("left", context))
            right_task = tasks.create_task(_compose_pose("right", context))
    except (ExceptionGroup, TimeoutError) as exc:
        raise PoseGenerationError("扶边姿态生成失败，请重试") from exc
    left, right = left_task.result(), right_task.result()
    return (
        PosePack(schema_version="spiritagent.2d.poses/1", left=left[0], right=right[0]),
        left[1] | right[1],
    )


async def generate_single_pose(reference: bytes, user_id: int | None, side: Side) -> tuple[Pose, dict[str, bytes]]:
    """单侧重生成一侧扶边姿态：与整包共用参考图预处理与色幕上下文，供外观级局部重生成调用。"""
    context = await _resolve_pose_context(reference, user_id)
    try:
        async with asyncio.timeout(1200):
            return await _compose_pose(side, context)
    except PoseGenerationError:
        raise
    except Exception as exc:
        raise PoseGenerationError("扶边姿态生成失败，请重试") from exc
