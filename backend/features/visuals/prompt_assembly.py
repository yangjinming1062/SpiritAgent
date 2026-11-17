"""按冻结素材装配视频请求；角色表演由视频模型根据目标自行决定。"""

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
        framing = ACTOR_VIDEO_LOOP_FRAMING if clip_kind == "loop" else ACTOR_VIDEO_REACTION_FRAMING
    else:
        framing = CINEMATIC_VIDEO_FRAMING
    # 旧检查点也可能保存逐部位脚本；角色视频始终从冻结目标装配，不转发该脚本。
    return VISUAL_VIDEO_MOTION_TEMPLATE.format(
        background=ACTOR_VIDEO_BACKGROUND if source.kind == "actor" else "环境与光线沿用首帧。",
        framing=framing,
        props=props,
        action=source.action.name,
        effect=source.action.motion_description,
        performance_role=_PERFORMANCE_EFFECTS[source.action.performance_role],
        motion_intensity=_MOTION_EFFECTS[source.action.motion_intensity],
        personality=source.persona.get("personality", "").strip() or "按参考角色自身的气质自然表现",
        requirements=source.requirements.strip() or "无",
        required_props=("、".join(required_props) or "无额外物品要求")
        if required_props is not None
        else "以活动目标与起始图为准",
        newer_requirements="视频调整要求" if progress.customization_priority == "video" else "整体调整要求",
        **common,
    )
