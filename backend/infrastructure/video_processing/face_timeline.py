"""在实际帧序列中选出可能短暂失焦或变形的面部证据。"""

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np

from .ffmpeg import VideoProcessError, raw_video_frames

type FaceBox = tuple[int, int, int, int]
type FaceTransition = tuple[int, int, int]


@dataclass(frozen=True)
class FaceTimelineEvidence:
    boxes: dict[int, FaceBox]
    transitions: tuple[FaceTransition, ...]
    transient_frames: tuple[int, ...]


def _interpolated_boxes(
    anchors: dict[int, FaceBox], indices: tuple[int, ...], times: tuple[float, ...]
) -> dict[int, FaceBox]:
    locations = dict(anchors)
    for start, end in pairwise(indices):
        if start not in anchors or end not in anchors or times[end] <= times[start]:
            continue
        a, b = np.asarray(anchors[start]), np.asarray(anchors[end])
        w, h = np.minimum(a[2:] - a[:2], b[2:] - b[:2])
        if np.any(np.abs((a[:2] + a[2:]) - (b[:2] + b[2:])) > (w, h)) or np.any(
            np.abs((a[2:] - a[:2]) - (b[2:] - b[:2])) > (w * 0.6, h * 0.6)
        ):
            continue
        for index in range(start + 1, end):
            weight = (times[index] - times[start]) / (times[end] - times[start])
            x, y, right, bottom = np.rint((1 - weight) * a + weight * b).astype(int).tolist()
            locations[index] = (x, y, right, bottom)
    return locations


def _face_signal(pixels: np.ndarray, box: FaceBox, scale: float) -> tuple[np.ndarray, float] | None:
    left, top, right, bottom = (round(coord * scale) for coord in box)
    inset_x, inset_y = round((right - left) * 0.24), round((bottom - top) * 0.20)
    face = pixels[top + inset_y : bottom - inset_y, left + inset_x : right - inset_x]
    if min(face.shape) < 12:
        return None
    luma = cv2.resize(face, (64, 64), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    luma = (luma - luma.mean()) / max(float(luma.std()), 16)
    fine = luma - cv2.GaussianBlur(luma, (0, 0), 1.2)
    return luma, float(np.abs(fine).mean())


def face_timeline_evidence(
    src: Path,
    size: tuple[int, int],
    times: tuple[float, ...],
    indices: tuple[int, ...],
    anchors: dict[int, FaceBox],
    decoder_args: tuple[str, ...],
) -> FaceTimelineEvidence:
    """只排序需要复看的真实帧。短时变化也可能来自眨眼、转头或遮挡。"""
    boxes = _interpolated_boxes(anchors, indices, times)
    if len(boxes) <= len(anchors):
        return FaceTimelineEvidence({}, (), ())
    scale = min(1.0, 768 / max(size))
    width, height = (max(2, round(side * scale)) for side in size)
    signals: dict[int, tuple[np.ndarray, float]] = {}
    count = 0
    with raw_video_frames(src, (width, height), pixel_format="gray", input_args=decoder_args) as frames:
        for index, frame in enumerate(frames):
            count = index + 1
            if index in boxes:
                pixels = np.frombuffer(frame, dtype=np.uint8).reshape(height, width)
                if signal := _face_signal(pixels, boxes[index], scale):
                    signals[index] = signal
    if count != len(times):
        raise VideoProcessError("面部扫描的解码帧与时间轴不一致")

    scores: list[tuple[bool, float, FaceTransition]] = []
    for index, (current, detail) in signals.items():
        for offset in (1, 3):
            previous, following = index - offset, index + offset
            if previous not in signals or following not in signals or times[following] - times[previous] > 0.30:
                continue
            before, before_detail = signals[previous]
            after, after_detail = signals[following]
            # 本帧比前后都软，或出现前后画面均无法解释的局部变化。
            nearby_detail = min(before_detail, after_detail)
            blur = (nearby_detail - detail) / max(nearby_detail, 0.05)
            neighbor_change = float(np.abs(before - after).mean())
            change = float(np.abs(current - (before + after) * 0.5).mean()) - 0.55 * neighbor_change
            score = max(blur / 0.30, change / 0.18)
            if score >= 1:
                # 两个紧邻帧接近，中间一帧却有显著结构变化或大幅丢失细节。
                transient = (
                    offset == 1
                    and times[following] - times[previous] <= 0.12
                    and neighbor_change <= 0.15
                    and (change >= 0.34 or (blur >= 0.55 and nearby_detail >= 0.08))
                )
                scores.append((transient, score, (previous, index, following)))
    selected: list[FaceTransition] = []
    evidence: dict[int, FaceBox] = {}
    transient_frames: list[int] = []
    for transient, _, transition in sorted(scores, reverse=True):
        if any(abs(times[transition[1]] - times[other[1]]) < 0.20 for other in selected):
            continue
        selected.append(transition)
        evidence.update({index: boxes[index] for index in transition})
        if transient:
            transient_frames.append(transition[1])
        if len(selected) == 2:
            break
    return FaceTimelineEvidence(evidence, tuple(sorted(selected)), tuple(sorted(transient_frames)))
