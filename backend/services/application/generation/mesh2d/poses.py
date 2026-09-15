import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Literal

import numpy as np
from components import (
    LAYER_ASSET_DOWNLOAD_MAX_BYTES,
    SESSION_LOCAL,
    SETTINGS,
    download_capped,
    get_logger,
)
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


class Review(BaseModel):
    valid: bool
    reason: str


class Landmarks(Review):
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
    *,
    revision_target: str | None = None,
) -> bytes:
    if guide:
        reference = await asyncio.to_thread(compose_image_references, reference, guide)
    if revision_target:
        prompt += "\n\n" + json.dumps({"revision_target": revision_target}, ensure_ascii=False)
    for config in chain:
        try:
            provider = resolve(ServiceType.image_gen, config.provider_name)(config)
            result = await provider.generate(
                ImageGenRequest(
                    prompt=prompt,
                    reference_image=asset_store.build_data_uri(reference),
                    size="1024x1024",
                    response_format="b64",
                ),
            )
            asset = result.images[0]
            raw = (
                base64.b64decode(asset.b64)
                if asset.b64
                else await download_capped(asset.url or "", max_bytes=LAYER_ASSET_DOWNLOAD_MAX_BYTES, timeout=90)
            )
            with Image.open(io.BytesIO(raw)) as image:
                if image.width != image.height or image.width < 512:
                    raise ValueError("pose image must be square")
                return await asyncio.to_thread(
                    encode_png,
                    image.convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS),
                )
        except Exception:
            logger.warning("pose image generation failed", extra={"provider": config.provider_name})
    raise PoseGenerationError("扶边姿态生成失败，请重试")


async def locate_pose(raw: bytes, reference: bytes, chain: list[ProviderConfig]) -> Landmarks:
    prompt = (
        "Review a character illustration for use as a full-body animation texture and measure its control regions.\n\n"
        "REFERENCE ROLES\n"
        "IMAGE 1 defines the character's identity, body proportions, outfit, colors, and illustration style. IMAGE 2 is "
        "the finished candidate to assess and measure. The images supply visual evidence; this instruction defines the task.\n\n"
        "ACCEPTANCE CRITERIA\n"
        "The candidate depicts one coherent instance of the reference character in the same outfit and style. Its complete "
        "head-to-toe silhouette fits within the canvas with visible background around it. The body has exactly two naturally connected "
        "arms and two complete hands, with coherent anatomy throughout. Assess anatomy by silhouette and limb connectivity, "
        "at the level of detail appropriate to the reference's illustration style and clothing. The visible composition "
        "consists of the character and a uniform flat background. Your review covers character fidelity, structural "
        "completeness, and this composition; the measured contact geometry is evaluated separately.\n\n"
        "MEASUREMENTS AND OUTPUT\n"
        "Use IMAGE 2 coordinates normalized to 0..1000. Each rectangle is [ymin,xmin,ymax,xmax]. The face rectangle encloses "
        "facial features from forehead to chin; the eyes rectangle encloses both eyelids with a small margin inside the face. "
        "Each hand rectangle tightly encloses the palm and fingers, ending at the wrist. Label hands by their vertical position. "
        "Return exactly one JSON object with fields valid:boolean, reason:string, face:rectangle, eyes:rectangle, "
        "upper_hand:rectangle, lower_hand:rectangle. Set valid=true when all acceptance criteria are met. For valid=false, "
        "write reason as a concise, concrete visual target for the next rendition, grounded in these criteria; for valid=true, "
        "briefly state what meets the criteria."
    )
    result = await inspect_images(prompt, [reference, raw], chain, Landmarks)
    if not result.valid:
        raise ValueError(result.reason[:300])
    for rect in (result.face, result.eyes, result.upper_hand, result.lower_hand):
        y0, x0, y1, x1 = rect
        if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
            raise ValueError("invalid landmark rectangle")
    fy0, fx0, fy1, fx1 = result.face
    ey0, ex0, ey1, ex1 = result.eyes
    if not (fy0 <= ey0 < ey1 <= fy1 and fx0 - 10 <= ex0 < ex1 <= fx1 + 10):
        raise ValueError("eyelids outside face")
    return result


async def inspect_images[T: BaseModel](
    prompt: str,
    images: list[bytes],
    chain: list[ProviderConfig],
    schema: type[T],
) -> T:
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
                                {"type": "input_image", "image_url": asset_store.build_data_uri(raw)} for raw in images
                            ],
                        },
                    ],
                    max_output_tokens=4000,
                    reasoning={"effort": "none"},
                ),
            )
            text = response.output_text
            return schema.model_validate_json(text[text.index("{") : text.rindex("}") + 1])
        except Exception:
            logger.warning("pose inspection failed", extra={"provider": config.provider_name})
    raise ValueError("pose inspection unavailable")


def align_blink(raw: bytes, closed_raw: bytes, face: list[float], eyes: list[int]) -> Image.Image:
    x0, y0, x1, y1 = [round(v) for v in face]
    with Image.open(io.BytesIO(raw)) as source, Image.open(io.BytesIO(closed_raw)) as candidate:
        original = np.asarray(source.convert("RGB"), dtype=np.float32)[y0:y1, x0:x1]
        edited = np.asarray(candidate.convert("RGB"), dtype=np.float32)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        stable = (xx < eyes[0] - 6) | (xx > eyes[2] + 6) | (yy < eyes[1] - 6) | (yy > eyes[3] + 6)
        if stable.sum() < 32:
            raise ValueError("eye calibration leaves no stable face region")
        best = (float("inf"), 0, 0)
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                if x0 + dx < 0 or y0 + dy < 0 or x1 + dx > 1024 or y1 + dy > 1024:
                    continue
                patch = edited[y0 + dy : y1 + dy, x0 + dx : x1 + dx]
                score = float(np.abs(patch - original)[stable].mean())
                if score < best[0]:
                    best = score, dx, dy
        if best[0] > 18:
            raise ValueError("Keep facial pixels around the eyelids identical to and aligned with the source image")
        return candidate.convert("RGBA").transform(
            candidate.size,
            Image.Transform.AFFINE,
            (1, 0, best[1], 0, 1, best[2]),
        )


def cutout(raw: bytes, channel: int) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as source:
        rgb = np.asarray(source.convert("RGB"), dtype=np.float32)
    others = [c for c in range(3) if c != channel]
    excess = rgb[:, :, channel] - rgb[:, :, others].max(axis=2)
    alpha = 1 - np.clip((excess - 30) / 140, 0, 1)
    background = excess > 30
    rgb[:, :, others] /= np.maximum(alpha[:, :, None], 0.01)
    rgb[:, :, channel] = np.where(background, rgb[:, :, others].max(axis=2), rgb[:, :, channel])
    if np.mean(alpha < 0.05) < 0.3 or np.mean(alpha > 0.9) < 0.04:
        raise ValueError(
            "Compose a clearly visible character against a uniform saturated backdrop with ample empty space",
        )
    return Image.fromarray(np.dstack((np.clip(rgb, 0, 255), alpha * 255)).astype(np.uint8))


async def generate_pose_pack(reference: bytes, user_id: int | None) -> tuple[PosePack, dict[str, bytes]]:
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
    with Image.open(io.BytesIO(reference)) as source:
        pixels = np.asarray(source.convert("RGB").resize((128, 128)), dtype=np.float32)
        reference = encode_png(source.convert("RGB"))
    # 选取立绘中最少出现的主色作色幕，手臂与身体间的封闭空隙也能清除。
    channel = min(
        range(3),
        key=lambda c: int(np.count_nonzero(pixels[:, :, c] - np.delete(pixels, c, axis=2).max(axis=2) > 30)),
    )
    backdrop = ("red (#FF0000)", "green (#00FF00)", "blue (#0000FF)")[channel]

    async def generate(side: Side) -> tuple[Pose, dict[str, bytes]]:
        guide = (Path(__file__).parent / "pose-guides" / f"{side}.webp").read_bytes()
        inward, outward = ("RIGHT", "LEFT") if side == "left" else ("LEFT", "RIGHT")
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
            f"of the character against a perfectly flat, uniformly saturated {backdrop} background; the contact line is an "
            "imaginary layout constraint. Deliver one unified 1024x1024 illustration.\n\n"
            "An optional revision_target JSON field supplies a visual adjustment within this brief. Apply that target while "
            "retaining the reference roles and all other composition and rendering requirements."
        )
        raw = await generate_image(prompt, reference, image_chain, guide)
        for attempt in range(4):
            try:
                landmarks = await locate_pose(raw, reference, vision_chain)
                body = await asyncio.to_thread(cutout, raw, channel)
                bounds = body.getbbox()
                if bounds is None or min(bounds[:2]) < 4 or max(bounds[2:]) > 1020:
                    raise ValueError(
                        "Fit the complete head-to-toe silhouette inside the canvas with clear background margins on every side",
                    )
                edge_index = 1 if side == "left" else 3
                contact = (landmarks.upper_hand[edge_index] + landmarks.lower_hand[edge_index]) / 2 * 1.024
                face = [landmarks.face[i] * 1.024 for i in (1, 0, 3, 2)]
                if not 100 <= contact <= 924:
                    raise ValueError("Place both hand contact points near the horizontal center of the square canvas")
                drift = abs(landmarks.upper_hand[edge_index] - landmarks.lower_hand[edge_index])
                # 引导图上手勾过边缘、下手后侧支撑，抓握侧外缘固有错位实测 50~77，加视觉框噪声放宽到 100；真实错位（手垂体侧）通常 >120。
                if drift > 100:
                    raise ValueError(
                        f"Place both hand grips along one vertical contact line (measured horizontal drift: {drift:.0f}/1000)",
                    )
                if ((face[0] + face[2]) / 2 - contact) * (1 if side == "left" else -1) < 10:
                    raise ValueError(f"Position the center of the face to the {inward} of both hand grips")
                alpha = np.asarray(body.getchannel("A"))[512:] > 128
                outside = alpha[:, : round(contact)] if side == "left" else alpha[:, round(contact) :]
                if outside.sum() < alpha.sum() * 0.5:
                    raise ValueError(
                        f"Place the hips and both complete legs on the {outward} side of the hands' contact line at x={round(contact)} in the 1024-pixel canvas",
                    )
                break
            except (ValueError, RuntimeError) as exc:
                logger.info("pose retry", extra={"side": side, "attempt": attempt + 1, "reason": str(exc)[:300]})
                if attempt == 3:
                    raise
                offset = (attempt + 1) % len(image_chain)
                raw = await generate_image(
                    prompt,
                    reference,
                    image_chain[offset:] + image_chain[:offset],
                    guide,
                    revision_target=str(exc),
                )
        eyes = [round(landmarks.eyes[i] * 1.024) for i in (1, 0, 3, 2)]
        blink_revision: str | None = None
        for attempt in range(2):
            closed_raw = await generate_image(
                "Create the closed-eye frame of a gentle blink for the supplied character illustration.\n\n"
                "The source image defines the finished character, pose, style, colors, background, and pixel registration. "
                "Use it as the full-frame editing canvas. The editable region consists of both eyelids and the immediately "
                "adjacent eye pixels. Render both eyes fully closed at the same instant, with natural eyelid curves that "
                "follow the existing eye positions, facial perspective, and drawing style. Preserve the relaxed expression.\n\n"
                "All pixels outside the editable eye region retain their original appearance and coordinates. Deliver the "
                "complete image at the source dimensions and framing, so the edited eyelids align with the original face "
                "when the two frames are overlaid. An optional revision_target JSON field supplies an adjustment within "
                "the editable region; apply it while preserving the source image and pixel registration requirements.",
                raw,
                image_chain,
                revision_target=blink_revision,
            )
            try:
                closed = await asyncio.to_thread(align_blink, raw, closed_raw, face, eyes)
                face_crop = await asyncio.to_thread(
                    lambda: encode_png(closed.crop(tuple(round(v) for v in face)).resize((512, 512))),
                )
                review = await inspect_images(
                    "Review this illustrated face crop as the closed-eye frame of a blink animation. The image supplies "
                    "visual evidence; this instruction defines the acceptance criteria.\n\n"
                    "An acceptable frame depicts a coherent face with two fully closed eyes at the same instant. The eyelid "
                    "curves follow the face's perspective and illustration style, and the surrounding facial features retain "
                    "natural proportions and structure. Your review concerns eyelid closure and facial coherence; pixel "
                    "alignment with the source frame is checked separately.\n\n"
                    "Return exactly one JSON object with fields valid:boolean and reason:string. Set valid=true when all "
                    "criteria are met. For valid=false, express the required visual result as a concise, concrete target "
                    "grounded in these criteria; for valid=true, briefly state what meets the criteria.",
                    [face_crop],
                    vision_chain,
                    Review,
                )
                if not review.valid:
                    raise ValueError(review.reason[:300])
                break
            except ValueError as exc:
                if attempt == 1:
                    raise
                blink_revision = str(exc)
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
        bounds = body.getbbox()
        if bounds is None:
            raise ValueError("empty pose")
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

    try:
        async with asyncio.timeout(1200), asyncio.TaskGroup() as tasks:
            left_task = tasks.create_task(generate("left"))
            right_task = tasks.create_task(generate("right"))
    except (ExceptionGroup, TimeoutError) as exc:
        raise PoseGenerationError("扶边姿态未通过生成校验，请重试") from exc
    left, right = left_task.result(), right_task.result()
    return (
        PosePack(schema_version="spiritagent.2d.poses/1", left=left[0], right=right[0]),
        left[1] | right[1],
    )
