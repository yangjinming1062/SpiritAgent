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


def align_blink(
    raw: bytes,
    closed_raw: bytes,
    face: list[float],
    eyes: list[int],
    width: int,
    height: int,
) -> Image.Image:
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
                if x0 + dx < 0 or y0 + dy < 0 or x1 + dx > width or y1 + dy > height:
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


def _analyze_chroma(
    rgb: NDArray[np.float32],
    channel: int,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_], NDArray[np.float32]]:
    """色幕背景分析：返回 (边框泛洪背景, 近边框主色硬掩码, 色键 alpha)。

    背景色取自粗化图的边框环：生成要求角色四周留白，边框环几乎全为背景；棋盘格
    等纹理粗化后摊平为均值色。候选判据只认「颜色仍近似边框背景」，不用色幕通道差
    作泛洪条件——服装主色与色幕同通道时洪水会灌进角色。
    """
    others = [c for c in range(3) if c != channel]
    excess = rgb[:, :, channel] - rgb[:, :, others].max(axis=2)
    small = np.asarray(
        Image.fromarray(rgb.astype(np.uint8)).resize(
            (rgb.shape[1] // 4, rgb.shape[0] // 4),
            Image.Resampling.BOX,
        ),
        dtype=np.float32,
    )
    border = np.concatenate([small[0], small[-1], small[:, 0], small[:, -1]])
    bg = np.median(border, axis=0)
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
    near_bg = np.sqrt(((rgb - bg) ** 2).sum(axis=2)) < 45
    # 色键 alpha 负责软过渡与残余清理：泛洪区整片清除，近背景色的封闭空隙（臂弯）也清除。
    # 封闭空隙颜色偏离背景色时，退回原色键逻辑（excess 阈值）——色幕纯色下空隙通道差大，能清干净。
    alpha = 1 - np.clip((excess - 30) / 140, 0, 1)
    alpha[near_bg] = 0
    alpha[flooded_full] = 0
    return flooded_full, near_bg, alpha


def cutout(raw: bytes, channel: int) -> Image.Image:
    """色幕抠图：与画布边框连通的背景整片泛洪清除（容忍色幕漂移、渐变与棋盘格纹理），封闭空隙退回色键。"""
    with Image.open(io.BytesIO(raw)) as source:
        rgb = np.asarray(source.convert("RGB"), dtype=np.float32)
    others = [c for c in range(3) if c != channel]
    _, _, alpha = _analyze_chroma(rgb, channel)
    background = alpha == 0
    rgb[:, :, others] /= np.maximum(alpha[:, :, None], 0.01)
    rgb[:, :, channel] = np.where(background, rgb[:, :, others].max(axis=2), rgb[:, :, channel])
    return Image.fromarray(np.dstack((np.clip(rgb, 0, 255), alpha * 255)).astype(np.uint8))


def _clear_disconnected_islands(alpha: NDArray[np.float32]) -> NDArray[np.float32]:
    """清除与主体不相连的不透明残留岛。

    种子取不透明质心并吸附到附近不透明像素，再沿不透明区 4 邻域闭包；质心可能落在
    肢体空隙，窗口吸附避免误把整块主体判成岛。连通残片（与角色粘连的背景框）不在
    此步范围，由色幕泛洪/近色掩码强制清除。
    """
    opaque = alpha > 127
    if int(opaque.sum()) < 64:
        return alpha
    ys, xs = np.nonzero(opaque)
    cy, cx = int(ys.mean()), int(xs.mean())
    if not opaque[cy, cx]:
        height, width = opaque.shape
        best: tuple[int, int] | None = None
        best_d2 = 0
        for y in range(max(0, cy - 32), min(height, cy + 33)):
            for x in range(max(0, cx - 32), min(width, cx + 33)):
                if not opaque[y, x]:
                    continue
                d2 = (y - cy) ** 2 + (x - cx) ** 2
                if best is None or d2 < best_d2:
                    best, best_d2 = (y, x), d2
        if best is None:
            return alpha
        cy, cx = best
    reach = np.zeros_like(opaque)
    reach[cy, cx] = True
    while True:
        grown = reach | (opaque & _dilate4(reach))
        if (grown == reach).all():
            break
        reach = grown
    cleaned = alpha.copy()
    cleaned[opaque & ~reach] = 0
    return cleaned


def _hybrid_alpha(
    isnet_alpha: NDArray[np.float32],
    flooded: NDArray[np.bool_],
    near_bg: NDArray[np.bool_],
    chroma_alpha: NDArray[np.float32],
) -> NDArray[np.float32]:
    """合并 ISNet 蒙版与色幕背景判定：明确背景强制透明，再清连通域外不透明岛。

    不用 min(isnet, chroma) 整图收缩——角色自带与色幕同通道的高光/光晕时，色键会
    误伤主体；只在色幕已判定为背景的位置把 alpha 打成 0。
    """
    alpha = isnet_alpha.copy()
    definite_bg = flooded | near_bg | (chroma_alpha < 0.05)
    alpha[definite_bg] = 0
    return _clear_disconnected_islands(alpha)


def matte_pose(raw: bytes, channel: int) -> Image.Image:
    """姿态图混合抠图：ISNet 显著性定主体边界，色幕泛洪/色键强制清除残留背景。

    生成链要求纯色幕，但供应商可能留下渐变、阴影或近景色块；原先 ISNet 成功即跳过
    色键，残留会整片进入贴边纹理。ISNet 缺失或推理失败时退回纯色幕 cutout。
    """
    body = subject_matte(raw)
    if body is None:
        return cutout(raw, channel)
    with Image.open(io.BytesIO(raw)) as source:
        rgb = np.asarray(source.convert("RGB"), dtype=np.float32)
    flooded, near_bg, chroma_alpha = _analyze_chroma(rgb, channel)
    alpha = np.asarray(body.getchannel("A"), dtype=np.float32)
    cleaned = _hybrid_alpha(alpha, flooded, near_bg, chroma_alpha)
    rgba = body.convert("RGBA")
    rgba.putalpha(Image.fromarray(np.clip(cleaned, 0, 255).astype(np.uint8)))
    return rgba


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


def _chroma_channel(pixels: NDArray[np.float32]) -> int:
    """选取立绘中最少出现的主色作色幕通道，手臂与身体间的封闭空隙也能清除。"""
    return min(
        range(3),
        key=lambda c: int(np.count_nonzero(pixels[:, :, c] - np.delete(pixels, c, axis=2).max(axis=2) > 30)),
    )


def pose_backdrop_for_artwork(reference: bytes) -> str:
    """按立绘主色选建议色幕（与生成链同规则），供自备图提示词给出纯色背景建议。"""
    pixels, _ = _prepare_pose_reference(reference)
    return _POSE_BACKDROPS[_chroma_channel(pixels)]


async def _resolve_pose_context(reference: bytes, user_id: int | None) -> _PoseContext:
    async with SESSION_LOCAL() as db:
        image_chain, _ = await resolve_image_gen_chain(
            db if user_id is not None else None,
            user_id,
            "reference",
        )
        vision_chain = await resolve_vision_chain(db if user_id is not None else None, user_id)
    if not image_chain or not vision_chain:
        raise PoseGenerationError("扶边姿态需要配置支持参考图的图像供应商及视觉模型")
    pixels, reference = await asyncio.to_thread(_prepare_pose_reference, reference)
    return _PoseContext(reference, image_chain, vision_chain, _chroma_channel(pixels))


def build_pose_side_prompt(
    side: Side,
    *,
    appearance: str = "",
    outfit_description: str = "",
    backdrop: str | None = None,
) -> str:
    """自备图场景的用户可见姿态提示词：以当前外观正面立绘为参考图锚定身份与穿着，
    姿势与构图规范与生成链一致，不引用内部概念。backdrop 是按立绘主色推荐的纯色背景
    （ISNet 抠图不依赖背景色，仅提升色键兜底与闭眼帧质量）；缺省时只要求纯色。"""
    inward, outward = ("RIGHT", "LEFT") if side == "left" else ("LEFT", "RIGHT")
    backdrop_text = backdrop or "flat solid"
    parts: list[str] = [
        "Redraw the character from the reference image as a full-body illustration for a peeking "
        "animation at a screen edge.",
        "",
        "CHARACTER",
        "The reference image is the identity and outfit anchor: keep the same character's face, species, "
        "body proportions, hairstyle, outfit, colors, and signature details, and the same illustration "
        "style. Do not replace the character or redesign the outfit.",
    ]
    if appearance.strip():
        parts.append(f"The character's appearance (supplementary): {appearance.strip()}.")
    if outfit_description.strip():
        parts.append(f"The character's current outfit: {outfit_description.strip()}.")
    parts.append("")
    parts.extend(
        (
            "POSE AND EXPRESSION",
            "Two naturally connected arms place their hands one above the other along an imaginary vertical contact "
            f"line near the canvas center. The head leans {inward} beyond the hands, while the hips and legs remain "
            f"on the {outward} side of that line. Both eyes are open, with a gentle, curious expression toward the "
            "viewer.",
            "",
            "COMPOSITION AND RENDERING",
            "Work on a square 1:1 canvas. Compose one complete character from the top of the hair to the tips of "
            "both feet. Keep the entire "
            "silhouette inside the frame, with at least 8% empty space above and below and clear space at both "
            "sides. Give every body region coherent anatomy, the character's own skin and clothing colors, and "
            "a consistent level of illustration detail. The visible image consists solely of the character against "
            f"a perfectly flat, uniformly saturated {backdrop_text} background with no texture, no gradient, no "
            "checkerboard pattern, no cast shadow, no vignette, and no glow spill onto the backdrop; keep the "
            "background pixel-uniform to the canvas border. The contact line is an imaginary layout constraint. "
            "Deliver one complete illustration.",
        ),
    )
    return "\n".join(parts)


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
        "gradient, no checkerboard pattern, no cast shadow, no vignette, and no glow spill onto the backdrop; keep the "
        "background pixel-uniform to the canvas border. The contact line is an imaginary layout constraint. Deliver one "
        "unified square 1:1 illustration."
    )
    raw = await generate_image(prompt, context.reference, context.image_chain, guide)
    return await _finish_pose(raw, side, context.image_chain, context.vision_chain, context.channel)


async def _finish_pose(
    raw: bytes,
    side: Side,
    image_chain: list[ProviderConfig],
    vision_chain: list[ProviderConfig],
    channel: int,
) -> tuple[Pose, dict[str, bytes]]:
    """主姿态图之后的共享后处理：抠图 → 关键点定位 → 闭眼帧 → 纹理编码；自备图路径复用。

    几何按图像实际宽高参数化：AI 路径恒为 1024×1024（generate_image 归一化），自备图保持
    用户原始尺寸与比例——渲染端网格、画布与布局均消费 Pose.width/height，无方形假设。"""
    # 混合抠图：ISNet 显著性定边界 + 色幕泛洪/色键强制清残留；模型缺失时内部退回色键。
    body = await asyncio.to_thread(matte_pose, raw, channel)
    bounds = body.getbbox()
    if bounds is None:
        raise ValueError("empty pose")
    width, height = body.size
    landmarks = await locate_pose(raw, vision_chain)
    edge_index = 1 if side == "left" else 3

    # locate_pose 输出归一化到 0..1000 的 [ymin,xmin,ymax,xmax]；经 (1,0,3,2) 重排为 [x0,y0,x1,y1]
    # 后，x 坐标按宽、y 坐标按高换算回像素（AI 方形路径下即原 ×1.024）。
    def _to_px(rect: Rect, *, as_int: bool = False) -> list[float]:
        scaled = [rect[i] * (height / 1000 if i % 2 == 0 else width / 1000) for i in (1, 0, 3, 2)]
        return [float(round(v)) for v in scaled] if as_int else scaled

    contact = (landmarks.upper_hand[edge_index] + landmarks.lower_hand[edge_index]) / 2 * (width / 1000)
    face = _to_px(landmarks.face)
    eyes = _to_px(landmarks.eyes, as_int=True)
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
        image_chain,
    )
    with Image.open(io.BytesIO(closed_raw)) as closed_source:
        # 编辑链恒按 1024×1024 返回；非方形输入先整体缩放回原尺寸，保持与原图同一像素配准
        closed_scaled = await asyncio.to_thread(
            encode_png,
            closed_source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS),
        )
    closed = await asyncio.to_thread(align_blink, raw, closed_scaled, face, eyes, width, height)
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
    hands = [_to_px(rect) for rect in (landmarks.upper_hand, landmarks.lower_hand)]
    return Pose(
        width=width,
        height=height,
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


async def compose_single_pose_from_image(
    raw: bytes,
    user_id: int | None,
    side: Side,
) -> tuple[Pose, dict[str, bytes]]:
    """自备图单侧姿态：用户提供的姿态图直接进入既有后处理（抠图/定位/闭眼帧/编码），
    只跳过主姿态图的生图调用。ISNet 抠图容忍任意背景，色幕通道按用户图自身主色选取供色键兜底。"""
    async with SESSION_LOCAL() as db:
        image_chain, _ = await resolve_image_gen_chain(
            db if user_id is not None else None,
            user_id,
            "reference",
        )
        vision_chain = await resolve_vision_chain(db if user_id is not None else None, user_id)
    if not image_chain or not vision_chain:
        raise PoseGenerationError("扶边姿态需要配置支持参考图的图像供应商及视觉模型")
    # 关键点换算、闭眼帧对齐与 Pose 尺寸记录均按图像实际宽高参数化（见 _finish_pose），
    # 用户图保持原始分辨率与比例进入后处理，不强行归一化到生图链的 1024×1024。
    pixels, _ = await asyncio.to_thread(_prepare_pose_reference, raw)
    channel = _chroma_channel(pixels)
    try:
        async with asyncio.timeout(1200):
            return await _finish_pose(raw, side, image_chain, vision_chain, channel)
    except PoseGenerationError:
        raise
    except Exception as exc:
        raise PoseGenerationError("扶边姿态生成失败，请重试") from exc
