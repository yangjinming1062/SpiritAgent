"""将已核对首帧的运动、资产类型及有效制作要求装配成视频请求。"""

from .prompts import (
    ACTOR_VIDEO_BACKGROUND,
    ACTOR_VIDEO_LOOP_FRAMING,
    ACTOR_VIDEO_REACTION_FRAMING,
    CINEMATIC_VIDEO_FRAMING,
    SCENE_VIDEO_MOTION_TEMPLATE,
    VISUAL_VIDEO_LOOP,
    VISUAL_VIDEO_MOTION_TEMPLATE,
    VISUAL_VIDEO_ONCE,
)
from .schemas import ActorClipSource, CinematicSource, SceneMediaSource, VisualJobProgress

_PERFORMANCE_EFFECTS = {
    "idle": "适合持续停留的安静陪伴画面",
    "sustained": "持续投入同一项活动的画面",
    "reaction": "完成一次活动或回应，并自然收束",
}
_MOTION_EFFECTS = {
    "subtle": "整体动态轻微、安定",
    "normal": "按日常自然的幅度表达",
    "expressive": "呈现清楚、舒展的较大动作",
}


def video_generation_prompt(
    source: ActorClipSource | CinematicSource | SceneMediaSource,
    progress: VisualJobProgress,
    *,
    aspect_ratio: str,
    feedback: str,
    video_feedback: str,
) -> str:
    clip_kind = "loop" if source.kind == "scene" else source.action.clip_kind
    common = {
        "duration_seconds": progress.duration_seconds,
        "aspect_ratio": aspect_ratio,
        "cycle": VISUAL_VIDEO_LOOP if clip_kind == "loop" else VISUAL_VIDEO_ONCE,
        "feedback": feedback.strip() or "无",
        "video_feedback": video_feedback.strip() or "无",
        "customization_priority": progress.customization_priority,
    }
    if source.kind == "scene":
        return SCENE_VIDEO_MOTION_TEMPLATE.format(motion=progress.motion_prompt, **common)
    required_props = progress.creation_plan.required_props if progress.creation_plan else None
    props = "使用首帧已有的物品，保持这些物品与角色的真实接触，不引入额外物品。"
    if source.kind == "actor" and required_props == []:
        props = "角色使用自身动作完成活动，首帧中空着的手保持空着，原有穿戴和随身物品随角色自然运动。"
    if source.kind == "actor":
        if source.opening_frame is not None or "sleeping" in source.action.semantic_tags:
            framing = (
                "保持首帧的固定全身景别、镜头位置和主体比例，连续保留身体支撑，醒来或安睡期间自然停留在原活动区域。"
            )
        else:
            framing = ACTOR_VIDEO_LOOP_FRAMING if clip_kind == "loop" else ACTOR_VIDEO_REACTION_FRAMING
    else:
        framing = CINEMATIC_VIDEO_FRAMING
    constraints = []
    if required_props:
        constraints.append("动作所需物品：" + "、".join(required_props))
    if source.requirements.strip():
        constraints.append("制作要求：" + source.requirements.strip())
    adjustments = [("整体调整", feedback.strip()), ("视频调整", video_feedback.strip())]
    if progress.customization_priority == "general":
        adjustments.reverse()
    constraints.extend(f"{label}：{value}" for label, value in adjustments if value)
    if all(value for _, value in adjustments):
        constraints.append("两项调整仅在冲突处以后列要求为准。")
    return VISUAL_VIDEO_MOTION_TEMPLATE.format(
        motion=progress.motion_prompt.strip() or source.action.motion_description,
        background=ACTOR_VIDEO_BACKGROUND if source.kind == "actor" else "环境与光线沿用首帧。",
        framing=framing,
        props=props,
        action=source.action.name,
        performance_role=_PERFORMANCE_EFFECTS[source.action.performance_role],
        motion_intensity=_MOTION_EFFECTS[source.action.motion_intensity],
        constraints="".join("\n" + line for line in constraints),
        **common,
    )
