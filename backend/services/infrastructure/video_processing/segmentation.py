"""角色视频动作分割：空白间隔（alpha 覆盖率）优先，运动能量兜底。

视频提示词要求动作间以「角色完全离开画面」的空白间隔分隔；首选按覆盖率切出间隔，
段数不符时用帧间差分运动能量（物种无关）做合并与切点吸附，尽力挽救每次付费生成，
只在两级策略都无法得到足数、足长的段时失败。处理参数由代码构造，外部输入只有文件路径。
"""

import statistics
from dataclasses import dataclass
from pathlib import Path

from components import get_logger

from .ffmpeg import VideoProcessError, _binary, _run, probe_video

logger = get_logger(__name__)

# 覆盖率扫描：低分辨率 RGBA 足够判定「画面里有没有角色」
_COVERAGE_WIDTH = 64
_COVERAGE_ALPHA_THRESHOLD = 24

# 空白间隔判定：连续近零覆盖 ≥ 最少帧数（约 0.2s）视为分隔
_BLANK_COVERAGE_MAX = 0.02
_MIN_GAP_SECONDS = 0.2

# 段有效性
MIN_SEGMENT_SECONDS = 0.5
# 段边界进出场修剪：段首/段尾覆盖率低于段内中位数该比例的帧被视作出入场过渡
_EDGE_RAMP_RATIO = 0.5
_EDGE_TRIM_MAX_SECONDS = 0.8

# 运动能量兜底：低分辨率灰度帧间差分（物种无关），切点吸附窗口约 0.25s
_MOTION_FRAME_WIDTH = 128
_MOTION_SNAP_WINDOW_SECONDS = 0.25


@dataclass(frozen=True)
class SegmentationResult:
    """按脚本动作顺序排列的段区间（秒，基于源视频时间轴）。"""

    segments: list[tuple[float, float]]
    method: str  # blank_gaps | motion


def _decode_frames_raw(src: Path, *, width: int, pix_fmt: str) -> tuple[bytes, int, int, float]:
    """低分辨率逐帧解码为裸像素；返回 (字节流, 宽, 高, fps)。"""
    probe = probe_video(src)
    scale_h = max(2, 2 * round(probe.height * width / probe.width / 2))
    args = [
        "-i",
        str(src),
        "-vf",
        f"scale={width}:{scale_h}:flags=area",
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix_fmt,
        "-",
    ]
    proc = _run([_binary("ffmpeg"), "-v", "error", *args])
    if proc.returncode != 0:
        raise VideoProcessError("动作分割读取失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    return proc.stdout, width, scale_h, probe.fps


def alpha_coverage_series(src: Path) -> tuple[list[float], float]:
    """逐帧 alpha 覆盖率（低分辨率统计）：返回 (每帧覆盖率, fps)。"""
    raw, scale_w, scale_h, fps = _decode_frames_raw(src, width=_COVERAGE_WIDTH, pix_fmt="rgba")
    frame_size = scale_w * scale_h * 4
    if len(raw) < frame_size:
        raise VideoProcessError("动作分割读取失败", internal=f"short output {len(raw)}")

    coverage: list[float] = []
    threshold = _COVERAGE_ALPHA_THRESHOLD
    for offset in range(0, len(raw) - frame_size + 1, frame_size):
        # 只取每帧的 alpha 平面（步长 4），统计高于阈值的像素占比
        alphas = raw[offset + 3 : offset + frame_size : 4]
        hit = sum(1 for a in alphas if a >= threshold)
        coverage.append(hit / (scale_w * scale_h))
    return coverage, fps


def motion_series(src: Path) -> tuple[list[float], float]:
    """逐帧运动能量（低分辨率灰度帧间差分均值，物种无关）：返回 (每帧运动量, fps)。

    首帧运动量记 0；返回序列长度与帧数一致，索引可直接与覆盖率序列对齐。"""
    import numpy as np

    raw, scale_w, scale_h, fps = _decode_frames_raw(src, width=_MOTION_FRAME_WIDTH, pix_fmt="gray")
    frame_size = scale_w * scale_h
    frame_count = len(raw) // frame_size
    if frame_count < 2:
        return [0.0] * frame_count, fps
    frames = np.frombuffer(raw, np.uint8, frame_count * frame_size).reshape(frame_count, scale_h, scale_w)
    diffs = np.abs(frames[1:].astype(np.int16) - frames[:-1].astype(np.int16)).mean(axis=(1, 2))
    return [0.0, *diffs.tolist()], fps


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """True 连续区段（半开区间 [start, end)）。"""
    runs: list[tuple[int, int]] = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(flags)))
    return runs


def _segments_from_blank_gaps(coverage: list[float], fps: float) -> list[tuple[int, int]]:
    """空白间隔之间的实体帧区段；首尾的空白（片头/片尾留白）忽略。"""
    min_gap_frames = max(3, int(round(_MIN_GAP_SECONDS * fps)))
    blank_flags = [value <= _BLANK_COVERAGE_MAX for value in coverage]
    gaps = [run for run in _runs(blank_flags) if run[1] - run[0] >= min_gap_frames]
    if not gaps:
        return []
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for gap_start, gap_end in gaps:
        if gap_start - cursor >= 2:
            bounds.append((cursor, gap_start))
        cursor = gap_end
    if len(coverage) - cursor >= 2:
        bounds.append((cursor, len(coverage)))
    return bounds


def _trim_edge_ramps(
    segment: tuple[int, int],
    coverage: list[float],
    fps: float,
) -> tuple[int, int]:
    """去掉段首/段尾的出入场过渡帧（走出/走回画面时覆盖率爬升的部分）。"""
    start, end = segment
    max_trim = int(round(_EDGE_TRIM_MAX_SECONDS * fps))
    body = coverage[start:end]
    if not body:
        return segment
    median = statistics.median(body)
    if median <= _BLANK_COVERAGE_MAX:
        return segment
    ramp_threshold = median * _EDGE_RAMP_RATIO

    trimmed_start = start
    for offset in range(min(max_trim, len(body))):
        if body[offset] >= ramp_threshold:
            trimmed_start = start + offset
            break

    trimmed_end = end
    for offset in range(min(max_trim, len(body))):
        if body[len(body) - 1 - offset] >= ramp_threshold:
            trimmed_end = end - offset
            break

    return trimmed_start, trimmed_end


def _to_seconds_segments(bounds: list[tuple[int, int]], fps: float) -> list[tuple[float, float]]:
    return [(max(0.0, start / fps), end / fps) for start, end in bounds if end > start]


def segment_actions(src: Path, action_count: int) -> SegmentationResult:
    """级联分割：空白间隔直接命中即返回；否则进入运动能量兜底。

    只在两级策略都失败（段数不足 / 存在退化段）时抛错，公开文案提示用户重新生成。
    """
    if action_count <= 0:
        raise VideoProcessError("动作数量无效", internal=f"action_count={action_count}")
    coverage, fps = alpha_coverage_series(src)
    if not any(value > _BLANK_COVERAGE_MAX for value in coverage):
        raise VideoProcessError("视频中未检测到可见角色，请重新生成视频形象")

    bounds = _segments_from_blank_gaps(coverage, fps)
    if len(bounds) == action_count:
        trimmed = [_trim_edge_ramps(segment, coverage, fps) for segment in bounds]
        segments = _to_seconds_segments(trimmed, fps)
        if all(end - start >= MIN_SEGMENT_SECONDS for start, end in segments):
            return SegmentationResult(segments=segments, method="blank_gaps")
        logger.info("blank gap segments too short; falling back to motion segmentation")

    return _segment_by_motion(src, coverage, fps, action_count)


def _segment_by_motion(
    src: Path,
    coverage: list[float],
    fps: float,
    action_count: int,
) -> SegmentationResult:
    """运动能量兜底（物种无关）：段数过多时合并衔接最静止的相邻段，
    不足时偶分切分并吸附到运动局部极小帧；切分前先修剪段边界的出入场过渡。"""
    motion, _motion_fps = motion_series(src)
    if len(motion) < max(action_count * 2, int(fps)):
        raise VideoProcessError(
            "无法从视频中识别出动作分段，请重新生成视频形象",
            internal=f"motion frames={len(motion)}",
        )

    min_gap_frames = max(3, int(round(_MIN_GAP_SECONDS * fps)))
    blank_flags = [value <= _BLANK_COVERAGE_MAX for value in coverage]
    gaps = [run for run in _runs(blank_flags) if run[1] - run[0] >= min_gap_frames]

    regions: list[tuple[int, int]] = []
    cursor = 0
    for gap_start, gap_end in gaps:
        if gap_start - cursor >= 2:
            regions.append((cursor, gap_start))
        cursor = gap_end
    if len(motion) - cursor >= 2:
        regions.append((cursor, len(motion)))
    if not regions:
        regions = [(0, len(motion))]

    # 段边界先修剪出入场过渡帧（走出/走回画面时覆盖率爬升的部分）
    trimmed: list[tuple[int, int]] = []
    for region in regions:
        start, end = _trim_edge_ramps(region, coverage, fps)
        if end - start >= 2:
            trimmed.append((start, end))
    regions = trimmed or regions

    while len(regions) > action_count:
        merged_at = _cheapest_merge(regions, motion)
        if merged_at is None:
            break
        left = regions[merged_at]
        right = regions[merged_at + 1]
        regions[merged_at : merged_at + 2] = [(left[0], right[1])]

    while len(regions) < action_count:
        index = max(range(len(regions)), key=lambda i: regions[i][1] - regions[i][0])
        split = _split_region(motion, regions[index][0], regions[index][1], 2, fps)
        if split is None:
            break
        regions[index : index + 1] = split

    if len(regions) != action_count:
        raise VideoProcessError(
            f"无法从视频中分割出 {action_count} 个动作，请重新生成视频形象",
            internal=f"regions={len(regions)} expected={action_count}",
        )

    segments = _to_seconds_segments(regions, fps)
    if any(end - start < MIN_SEGMENT_SECONDS for start, end in segments):
        raise VideoProcessError(
            "动作分段过短，请重新生成视频形象",
            internal=f"segments={segments}",
        )
    if any(
        sum(value > _BLANK_COVERAGE_MAX for value in coverage[start:end]) < MIN_SEGMENT_SECONDS * fps
        for start, end in regions
    ):
        raise VideoProcessError("动作分段缺少可见角色，请重新生成视频形象")
    return SegmentationResult(segments=segments, method="motion")


def _cheapest_merge(regions: list[tuple[int, int]], motion: list[float]) -> int | None:
    """找出相邻段衔接处运动最小的合并位置；无候选时退回首对。"""
    best_index: int | None = None
    best_motion = None
    for index in range(len(regions) - 1):
        boundary = regions[index][1]
        boundary_motion = motion[boundary] if 0 <= boundary < len(motion) else 0.0
        if best_motion is None or boundary_motion < best_motion:
            best_index, best_motion = index, boundary_motion
    return best_index


def _snap_cut_to_still(motion: list[float], ideal: int, low: int, high: int) -> int:
    """把切点吸附到 [low, high] 窗口内运动能量最小的帧（动作衔接更自然）。"""
    best, best_motion = ideal, None
    for index in range(max(low, 0), min(high, len(motion) - 1) + 1):
        if best_motion is None or motion[index] < best_motion:
            best, best_motion = index, motion[index]
    return best


def _split_region(
    motion: list[float],
    start: int,
    end: int,
    parts: int,
    fps: float,
) -> list[tuple[int, int]] | None:
    """区间 [start, end) 切成 parts 段：偶分切点吸附到运动能量局部极小，保证每段 ≥ 最小段长。"""
    length = end - start
    min_frames = max(2, int(round(MIN_SEGMENT_SECONDS * fps)))
    if parts <= 1 or length < min_frames * parts:
        return [(start, end)] if parts == 1 else None
    snap = int(round(_MOTION_SNAP_WINDOW_SECONDS * fps))

    cuts: list[int] = []
    for index in range(1, parts):
        lower = cuts[-1] + min_frames if cuts else start + min_frames
        upper = end - min_frames * (parts - index)
        if lower > upper:
            return None
        ideal = min(max(start + int(round(index * length / parts)), lower), upper)
        cuts.append(_snap_cut_to_still(motion, ideal, max(lower, ideal - snap), min(upper, ideal + snap)))
    bounds = [start, *cuts, end]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
