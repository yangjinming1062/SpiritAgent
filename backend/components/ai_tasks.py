"""产品模型调用任务目录：任务是链配置与继承的单位；环节只作管理页展示说明。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AiTaskKey(StrEnum):
    companion_chat = "companion_chat"
    work_chat = "work_chat"
    scheduled_task = "scheduled_task"
    proactive = "proactive"
    memory_review = "memory_review"
    memory_embedding = "memory_embedding"
    nightly = "nightly"
    posts = "posts"
    appearance = "appearance"
    outfit = "outfit"
    scene = "scene"
    window_action = "window_action"
    desktop_life = "desktop_life"
    speech_input = "speech_input"
    companion_voice = "companion_voice"
    speech_preview = "speech_preview"
    voice_design = "voice_design"


class AiTaskDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AiTaskKey
    group: str
    label: str
    services: tuple[str, ...]
    steps: tuple[str, ...]


_CHAT_STEPS = (
    "主回合",
    "会话标题",
    "上下文压缩",
    "网页内容摘要",
    "子 Agent 委派",
    "聊天图片与重做",
    "聊天验图",
    "聊天视频",
)


def _task(
    task: AiTaskKey,
    group: str,
    label: str,
    services: tuple[str, ...],
    steps: tuple[str, ...],
) -> AiTaskDefinition:
    return AiTaskDefinition(id=task, group=group, label=label, services=services, steps=steps)


AI_TASKS: tuple[AiTaskDefinition, ...] = (
    _task(
        AiTaskKey.companion_chat,
        "聊天",
        "陪伴聊天",
        ("llm", "image_gen", "video_gen"),
        ("主回合", "心情更新", "即时记忆提案", *_CHAT_STEPS[1:]),
    ),
    _task(
        AiTaskKey.work_chat,
        "聊天",
        "工作与专业预设聊天",
        ("llm", "image_gen", "video_gen"),
        ("主回合", "即时记忆提案", *_CHAT_STEPS[1:]),
    ),
    _task(AiTaskKey.scheduled_task, "聊天", "普通定时任务", ("llm", "image_gen", "video_gen"), _CHAT_STEPS),
    _task(
        AiTaskKey.proactive,
        "主动陪伴",
        "主动陪伴",
        ("llm", "image_gen", "video_gen"),
        (
            "主动陪伴回合",
            "自主移动判断",
            "窗口空闲动作选择",
            "桌面生活动作选择",
            "即时记忆提案",
            *_CHAT_STEPS[1:],
        ),
    ),
    _task(AiTaskKey.memory_review, "记忆", "记忆审阅", ("llm",), ("回合后与定期记忆审阅",)),
    _task(AiTaskKey.memory_embedding, "记忆", "记忆向量", ("embedding",), ("记忆入库与查询向量",)),
    _task(
        AiTaskKey.nightly,
        "夜间",
        "夜间",
        ("llm",),
        ("夜间记忆整理", "自主活动规划", "相处理解反思", "日记决定与创作"),
    ),
    _task(
        AiTaskKey.posts,
        "动态",
        "动态",
        ("llm", "image_gen", "video_gen", "tts"),
        (
            "发布决定与内容计划",
            "评论回复",
            "动态图片",
            "动态视频",
            "语音动态与旁白",
            "身份评分",
        ),
    ),
    _task(
        AiTaskKey.appearance,
        "形象与衣柜",
        "形象初始化",
        ("llm", "image_gen"),
        (
            "角色卡视觉特征提取",
            "头像提示词整理",
            "头像生成与重绘",
            "身体与姿态指导",
            "全身生成与重绘",
            "性格标签提取",
            "提示词审核改写",
        ),
    ),
    _task(
        AiTaskKey.outfit,
        "形象与衣柜",
        "换装",
        ("llm", "image_gen"),
        (
            "服装参考解析",
            "换装身体与姿态指导",
            "换装生成与微调",
            "造型命名与描述",
            "提示词审核改写",
            "身份评分",
        ),
    ),
    _task(
        AiTaskKey.scene,
        "场景",
        "场景",
        ("llm", "image_gen"),
        ("场景图片生成与重绘", "场景重复伙伴检查", "场景标题与描述"),
    ),
    _task(
        AiTaskKey.window_action,
        "窗口动作",
        "窗口动作",
        ("llm", "image_gen", "video_gen"),
        (
            "动作提案评审",
            "静态动作描述",
            "视频动作脚本",
            "外观参考校准图",
            "动作姿态图",
            "动作视频",
            "动作成品复核",
            "探身遮挡位置分析",
            "身份评分",
        ),
    ),
    _task(
        AiTaskKey.desktop_life,
        "桌面生活",
        "桌面生活",
        ("llm", "image_gen", "video_gen"),
        (
            "桌面动作提案评审",
            "生活构图与运动设计",
            "生活起始画面",
            "生活视频",
            "生活视频成品检查",
            "身份评分",
        ),
    ),
    _task(AiTaskKey.speech_input, "语音", "语音输入转写", ("stt",), ("语音输入转写",)),
    _task(AiTaskKey.companion_voice, "语音", "伙伴回复合成", ("tts",), ("伙伴回复合成",)),
    _task(AiTaskKey.speech_preview, "语音", "朗读与试听", ("tts",), ("朗读与试听",)),
    _task(AiTaskKey.voice_design, "语音", "自定义音色设计", ("tts",), ("自定义音色设计",)),
)
AI_TASK_BY_ID: dict[AiTaskKey, AiTaskDefinition] = {definition.id: definition for definition in AI_TASKS}
