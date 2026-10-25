"""影像视频的原生档位与目标画幅之间的尺寸匹配。"""

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from infrastructure.ai.providers.base import VideoGenProvider
from infrastructure.media_geometry import resolution_fit_key

from features.media.chain import FrozenMediaProvider


@dataclass(frozen=True)
class VisualVideoResolution:
    resolution: str
    fit: tuple[float, int, float]


@dataclass(frozen=True)
class VisualVideoPlan:
    provider: VideoGenProvider
    aspect_ratio: str
    duration: int
    resolution: VisualVideoResolution


def freeze_visual_video_plans(
    plans: list[VisualVideoPlan],
    *,
    preserve_order: bool,
    resolution_for: Callable[[VideoGenProvider, int, str], VisualVideoResolution | None],
) -> tuple[int, list[FrozenMediaProvider]] | None:
    if not plans:
        return None
    first = plans[0] if preserve_order else min(plans, key=lambda plan: plan.resolution.fit)
    seconds = first.duration
    frozen_plans: list[tuple[tuple[float, int, float], FrozenMediaProvider]] = []
    # 始终按配置顺序重算；时长冻结后同分仍由原顺序决定。
    for plan in plans:
        if plan.provider.durations is not None and seconds not in plan.provider.durations:
            continue
        choice = (
            plan.resolution if seconds == plan.duration else resolution_for(plan.provider, seconds, plan.aspect_ratio)
        )
        if choice is None:
            continue
        frozen = FrozenMediaProvider.from_config(plan.provider.config)
        frozen.video_resolution = choice.resolution
        frozen.video_aspect_ratio = plan.aspect_ratio
        frozen_plans.append((choice.fit, frozen))
    if not preserve_order:
        frozen_plans.sort(key=lambda plan: plan[0])
    return seconds, [frozen for _fit, frozen in frozen_plans]


def nearest_visual_video_resolution(
    provider: VideoGenProvider,
    *,
    duration: int,
    native_ratio: str,
    delivery_ratio: str,
    target_width: int,
    target_height: int,
    first_frame: bool = False,
    last_frame: bool = False,
) -> VisualVideoResolution | None:
    ratio = Fraction(delivery_ratio.replace(":", "/"))
    choices: list[VisualVideoResolution] = []
    for resolution in provider.available_resolutions(
        duration=duration,
        first_frame=first_frame,
        last_frame=last_frame,
    ):
        width, height = provider.resolution_size(resolution, native_ratio)
        try:
            fit = resolution_fit_key(
                width,
                height,
                target_width,
                target_height,
                keep_aspect=False,
                output_aspect=(ratio.numerator, ratio.denominator),
                alignment=2,
            )
        except ValueError:
            continue
        choices.append(VisualVideoResolution(resolution=resolution, fit=fit))
    return min(choices, key=lambda choice: choice.fit, default=None)
