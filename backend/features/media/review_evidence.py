"""落盘实际送检字节；续审只读取原证据，不重选参考或重采样。"""

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from itertools import batched, groupby
from pathlib import Path

from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.assets.asset_store import (
    build_data_uri,
    resolve_asset_reference,
    save_image_chain_input_asset_async,
    sniff_media_ext,
)
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .review_contracts import ReviewEvidence, ReviewImage, review_context

FACE_SEQUENCE_LABEL = "面部同屏对照，每行 R 是原始身份依据，编号为成片相应时刻"
FACE_MOTION_LABEL = "同一镜头的面部运动，每行 M 为原表演、F 为交付候选，列标注实际秒数"
FACE_TRANSITION_LABEL = "短时面部变化，每行 A、B、C 为同一时段的三个真实画格"
SCENE_TRANSITION_LABEL = "开场环境对照，逐列为实际时刻，上行是全帧，其余行放大对应画面的边缘区域"
FACE_DETAIL_PREFIX = "候选视频面部局部，"
_FULL_FRAME_PREFIX = "候选视频全帧，时间 "
_OVERVIEW_PREFIXES = {kind: f"候选视频{kind}，从左到右逐行对应 " for kind in ("全帧", "同刻面部局部")}
_OVERVIEW_LABELS = tuple(_OVERVIEW_PREFIXES.values())
_OVERVIEW_GROUP_SIZE = 12
_MAX_SINGLE_VIEWS = 4

type _TimedFrame = tuple[ReviewImage, float]


@dataclass(frozen=True)
class _TimelinePage:
    kind: str
    frames: tuple[_TimedFrame, ...]
    faces: tuple[_TimedFrame, ...]

    def label(self) -> str:
        times = "、".join(f"{time:g}" for _, time in self.frames)
        label = f"{_OVERVIEW_PREFIXES[self.kind]}{times} 秒"
        if self.faces:
            face_times = "、".join(f"{time:g}" for _, time in self.faces)
            label += f"。末行面部局部对应 {face_times} 秒"
        return label


def review_image_labels(
    reference_count: int, candidate_count: int, times: tuple[float, ...] = (), detail_times: tuple[float, ...] = ()
) -> tuple[str, ...]:
    if reference_count < 0 or candidate_count < 1 or (times and len(times) != candidate_count):
        raise ValueError("检查图片数量与时间轴不一致")
    return (
        *(f"参考图 {index + 1}（创作依据，不属于候选画面或视频时间轴）" for index in range(reference_count)),
        *(f"{_FULL_FRAME_PREFIX}{time:g} 秒" for time in times),
        *(f"候选画面 {index + 1}" for index in range(candidate_count) if not times),
        *(f"{FACE_DETAIL_PREFIX}取自 {time:g} 秒全帧（同一时刻，不是新镜头）" for time in detail_times),
    )


async def freeze_review_evidence(
    user_id: int,
    images: tuple[str, ...],
    *,
    prompt: str,
    context: object,
    media_path: str | None = None,
    labels: tuple[str, ...] = (),
) -> ReviewEvidence:
    if labels and len(labels) != len(images):
        raise ValueError("检查证据与图片标签数量不一致")
    entries: list[ReviewImage] = []
    for index, uri in enumerate(images):
        data, _ = await resolve_reference_bytes(uri)
        digest = hashlib.sha256(data).hexdigest()
        path = await save_image_chain_input_asset_async(
            data,
            user_id=user_id,
            generation_id=digest[:32],
            label="review",
            ext=sniff_media_ext(data),
            directory="reviews",
        )
        entries.append(ReviewImage(path=path, sha256=digest, role=labels[index] if labels else f"image_{index + 1}"))
    if media_path:
        media_hash = await asyncio.to_thread(asset_file_hash, media_path)
    else:
        media_hash = entries[-1].sha256 if entries else None
    return ReviewEvidence(
        media_hash=media_hash,
        policy_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        policy_prompt=prompt,
        context_json=review_context(context),
        images=tuple(entries),
    )


def _verified_image(entry: ReviewImage) -> tuple[bytes, str]:
    resolved = resolve_asset_reference(entry.path)
    if resolved is None:
        raise ValueError("检查证据无法读取")
    data = resolved[0].read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ValueError("检查证据已变化")
    return data, resolved[1]


def _read_image(entry: ReviewImage) -> str:
    return build_data_uri(*_verified_image(entry))


def _timed_view(entry: ReviewImage) -> str:
    if entry.role.startswith(_FULL_FRAME_PREFIX):
        return "全帧"
    return "同刻面部局部" if entry.role.startswith(FACE_DETAIL_PREFIX) else ""


def _timed_frame(entry: ReviewImage) -> _TimedFrame:
    prefix = _FULL_FRAME_PREFIX if entry.role.startswith(_FULL_FRAME_PREFIX) else FACE_DETAIL_PREFIX + "取自 "
    return entry, float(entry.role.removeprefix(prefix).split(" 秒", 1)[0])


def _timeline_sheet(page: _TimelinePage, max_width: int) -> str:
    columns = max(math.ceil(math.sqrt(len(page.frames))), len(page.faces))
    size = min(400 if page.kind == "全帧" else 240, max_width // columns)
    rows = math.ceil(len(page.frames) / columns)
    with Image.new("RGB", (columns * size, (rows + bool(page.faces)) * (size + 25)), "#edeae6") as sheet:
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default(size=17)
        for index, (entry, time) in enumerate((*page.frames, *page.faces)):
            if index >= len(page.frames):
                x, y = (index - len(page.frames)) * size, rows * (size + 25)
            else:
                x, y = (index % columns) * size, (index // columns) * (size + 25)
            with (
                Image.open(BytesIO(_verified_image(entry)[0])) as source,
                ImageOps.contain(source, (size, size), Image.Resampling.LANCZOS) as tile,
            ):
                sheet.paste(
                    tile,
                    (x + (size - tile.width) // 2, y + 24 + (size - tile.height) // 2),
                    tile.getchannel("A") if "A" in tile.getbands() else None,
                )
            draw.text((x + 6, y + 2), f"{time:.2f} s", fill="#202020", font=font)
        with BytesIO() as buf:
            sheet.save(buf, format="WEBP", lossless=True)
            return build_data_uri(buf.getvalue(), "image/webp")


async def freeze_video_overview(user_id: int, evidence: ReviewEvidence) -> ReviewEvidence:
    """从冻结原件推导整体布局，原件与专项索引保留原位。"""
    context = json.loads(evidence.context_json)
    previous = context.get("video_overview_indices")
    has_sequence = "face_sequence_index" in context
    focus_indices = {context[key] for key in ("face_transition_index", "scene_transition_index") if key in context}
    source_count = len(evidence.images)
    if previous is not None:
        # 整体拼图是追加到冻结原件和专项图之后的派生产物。
        while source_count and evidence.images[source_count - 1].role.startswith(_OVERVIEW_LABELS):
            source_count -= 1
        if not source_count:
            raise ValueError("视频检查证据缺少冻结原帧")
    if previous is None and source_count <= _MAX_SINGLE_VIEWS and not has_sequence and not focus_indices:
        return evidence
    images = evidence.images[:source_count]
    # 主体头部锚点使用实际首帧的归一化坐标，送检时须保留该帧的独立画布。
    anchor_frame = context.get("reference_frame_count") if context.get("isolated_subject") else None
    # 整体检查保留代表性局部，完整时序和制作底片交由专项检查。
    indices = [index for index in range(len(images)) if index not in focus_indices]
    if anchor_frame is not None and anchor_frame not in indices:
        indices = [*indices[:anchor_frame], anchor_frame, *indices[anchor_frame:]]
    if has_sequence:
        detail_indices = [i for i in indices if images[i].role.startswith(FACE_DETAIL_PREFIX)]
        keep_details = (
            {detail_indices[0], detail_indices[len(detail_indices) // 2], detail_indices[-1]}
            if detail_indices
            else set()
        )
        indices = [
            i
            for i in indices
            if i not in {context.get("face_motion_index"), context["face_sequence_index"]}
            and (not images[i].role.startswith(FACE_DETAIL_PREFIX) or i in keep_details)
        ]
    pages: list[_TimelinePage] = []
    if len(indices) > _MAX_SINGLE_VIEWS:
        retained = []
        face_samples = (
            tuple(images[i] for i in indices if images[i].role.startswith(FACE_DETAIL_PREFIX)) if has_sequence else ()
        )
        if face_samples and any(i != anchor_frame and _timed_view(images[i]) == "全帧" for i in indices):
            indices = [i for i in indices if not images[i].role.startswith(FACE_DETAIL_PREFIX)]
        else:
            face_samples = ()
        for kind, entries in groupby(indices, key=lambda i: "" if i == anchor_frame else _timed_view(images[i])):
            for group in batched(entries, _OVERVIEW_GROUP_SIZE):
                faces = face_samples if kind == "全帧" else ()
                if not kind or (len(group) == 1 and not faces):
                    retained.extend(group)
                    continue
                retained.append(source_count + len(pages))
                pages.append(
                    _TimelinePage(kind, tuple(_timed_frame(images[i]) for i in group), tuple(map(_timed_frame, faces)))
                )
                if faces:
                    face_samples = ()
        indices = retained
    labels = tuple(page.label() for page in pages)
    if indices == previous and labels == tuple(image.role for image in evidence.images[source_count:]):
        return evidence
    if previous is None and indices == list(range(source_count)) and not pages:
        return evidence
    derived: list[ReviewImage] = []
    # 面部专项保留原尺寸局部，整体检查限制时间图尺寸，为结论留出预算。
    width = 1024 if has_sequence else 1280
    for page, label in zip(pages, labels, strict=True):
        uri = await asyncio.to_thread(_timeline_sheet, page, width)
        packed = await freeze_review_evidence(
            user_id, (uri,), prompt=evidence.policy_prompt, context={}, labels=(label,)
        )
        derived.extend(packed.images)
    context = {key: value for key, value in context.items() if not key.startswith("video_overview_")}
    if indices != list(range(source_count)) or pages:
        context["video_overview_indices"] = indices
    return evidence.model_copy(update={"images": (*images, *derived), "context_json": review_context(context)})


def video_overview(evidence: ReviewEvidence) -> ReviewEvidence:
    context = json.loads(evidence.context_json)
    indices = context.get("video_overview_indices")
    if indices is None:
        return evidence
    if not indices or any(type(index) is not int or not 0 <= index < len(evidence.images) for index in indices):
        raise ValueError("视频送检顺序与证据不一致")
    context = {key: value for key, value in context.items() if not key.startswith("video_overview_")}
    context.pop("face_motion_index", None)
    context.pop("face_transition_index", None)
    context.pop("face_transition_policy", None)
    context.pop("face_transient_times", None)
    context.pop("scene_transition_index", None)
    context.pop("scene_transition_policy", None)
    face_sequence = context.pop("face_sequence_index", None)
    if face_sequence in indices:
        context["face_sequence_index"] = indices.index(face_sequence)
    return evidence.model_copy(
        update={"images": tuple(evidence.images[i] for i in indices), "context_json": review_context(context)}
    )


async def read_review_evidence(evidence: ReviewEvidence) -> tuple[str, ...]:
    if hashlib.sha256(evidence.policy_prompt.encode()).hexdigest() != evidence.policy_hash:
        raise ValueError("检查策略依据已变化")
    return tuple([await asyncio.to_thread(_read_image, entry) for entry in evidence.images])


@lru_cache(maxsize=2048)
def _file_hash(path: Path, version: tuple[int, ...]) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset_file_hash(reference: str) -> str | None:
    try:
        resolved = resolve_asset_reference(reference)
        if resolved is None:
            return None
        path = resolved[0]
        stat = path.stat()
        return _file_hash(path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    except OSError:
        return None


def review_evidence_intact(evidence: ReviewEvidence) -> bool:
    if hashlib.sha256(evidence.policy_prompt.encode()).hexdigest() != evidence.policy_hash:
        return False
    return all(asset_file_hash(entry.path) == entry.sha256 for entry in evidence.images)


def review_media_intact(evidence: ReviewEvidence, media_path: str) -> bool:
    return (
        evidence.media_hash is not None
        and review_evidence_intact(evidence)
        and asset_file_hash(media_path) == evidence.media_hash
    )
