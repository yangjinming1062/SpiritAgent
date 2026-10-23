"""产品模型调用任务目录：任务是链配置与继承的单位；能力用途只作管理页展示说明。"""

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
    visuals = "visuals"
    speech_input = "speech_input"
    companion_voice = "companion_voice"
    speech_preview = "speech_preview"
    voice_design = "voice_design"
    voice_clone = "voice_clone"


class AiTaskDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AiTaskKey
    group: str
    label: str
    services: tuple[str, ...]
    service_usage: dict[str, tuple[str, ...]]


_CHAT_LLM_USAGE = (
    "会话标题",
    "上下文压缩",
    "网页内容摘要",
    "委派子任务",
    "聊天图片检查与出镜身份检查",
)


def _task(
    task: AiTaskKey,
    group: str,
    label: str,
    service_usage: dict[str, tuple[str, ...]],
) -> AiTaskDefinition:
    return AiTaskDefinition(
        id=task,
        group=group,
        label=label,
        services=tuple(service_usage),
        service_usage=service_usage,
    )


AI_TASKS: tuple[AiTaskDefinition, ...] = (
    _task(
        AiTaskKey.companion_chat,
        "陪伴聊天",
        "陪伴聊天",
        {
            "llm": ("陪伴对话", "心情更新", "即时记忆提案", *_CHAT_LLM_USAGE),
            "image_gen": ("对话中的图片生成", "参考图编辑与图片重做"),
            "video_gen": ("对话中的视频生成",),
        },
    ),
    _task(
        AiTaskKey.work_chat,
        "专业预设",
        "专业预设",
        {
            "llm": ("专业预设对话", "即时记忆提案", *_CHAT_LLM_USAGE),
            "image_gen": ("专业预设中的图片生成", "参考图编辑与图片重做"),
            "video_gen": ("专业预设中的视频生成",),
        },
    ),
    _task(
        AiTaskKey.scheduled_task,
        "定时任务",
        "定时任务",
        {
            "llm": ("普通定时任务执行", *_CHAT_LLM_USAGE),
            "image_gen": ("定时任务中的图片生成", "参考图编辑与图片重做"),
            "video_gen": ("定时任务中的视频生成",),
        },
    ),
    _task(
        AiTaskKey.proactive,
        "主动陪伴",
        "主动陪伴",
        {
            "llm": (
                "主动陪伴回合",
                "自主移动判断",
                "窗口与桌面空闲动作选择",
                "即时记忆提案",
                *_CHAT_LLM_USAGE,
            ),
            "image_gen": ("主动陪伴中的图片生成", "参考图编辑与图片重做"),
            "video_gen": ("主动陪伴中的视频生成",),
        },
    ),
    _task(AiTaskKey.memory_review, "记忆", "记忆审阅", {"llm": ("陪伴与专业预设的回合后记忆审阅", "定期记忆审阅")}),
    _task(AiTaskKey.memory_embedding, "记忆", "记忆向量", {"embedding": ("记忆入库向量", "对话与记忆检索的查询向量")}),
    _task(
        AiTaskKey.nightly,
        "夜间",
        "夜间",
        {"llm": ("夜间记忆整理", "自主活动规划", "相处理解反思", "日记决定与创作")},
    ),
    _task(
        AiTaskKey.posts,
        "动态",
        "动态",
        {
            "llm": ("发布决定与内容计划", "评论回复", "出镜身份检查"),
            "image_gen": ("动态配图生成",),
            "video_gen": ("动态视频生成",),
            "tts": ("语音动态合成", "动态视频旁白"),
        },
    ),
    _task(
        AiTaskKey.appearance,
        "形象初始化",
        "形象初始化",
        {
            "llm": ("角色卡视觉特征提取", "头像提示词整理", "身体与姿态指导", "性格标签提取", "提示词审核改写"),
            "image_gen": ("头像生成与重绘", "全身形象生成与重绘"),
        },
    ),
    _task(
        AiTaskKey.outfit,
        "换装",
        "换装",
        {
            "llm": ("服装参考解析", "身体与姿态指导", "造型命名与描述", "提示词审核改写", "身份检查"),
            "image_gen": ("换装形象生成", "衣柜造型重绘与微调"),
        },
    ),
    _task(
        AiTaskKey.scene,
        "场景",
        "场景",
        {
            "llm": ("场景重复伙伴检查", "场景标题与描述"),
            "image_gen": ("场景图片生成与重绘", "初始化与夜间规划创建的场景"),
        },
    ),
    _task(
        AiTaskKey.visuals,
        "影像",
        "影像",
        {
            "llm": ("动作素材评审", "组合构图与运动设计", "影像成品及身份检查", "成品画面命名与着装识别"),
            "image_gen": ("影像起始画面",),
            "video_gen": ("影像视频", "夜间规划制作的影像视频"),
        },
    ),
    _task(AiTaskKey.speech_input, "语音", "输入转写", {"stt": ("桌面与手机的语音输入转写",)}),
    _task(
        AiTaskKey.companion_voice,
        "语音",
        "伙伴回复合成",
        {"tts": ("陪伴聊天与主动陪伴的语音回复", "伙伴可选音色目录")},
    ),
    _task(AiTaskKey.speech_preview, "语音", "朗读与试听", {"tts": ("文字消息朗读", "伙伴声音试听")}),
    _task(
        AiTaskKey.voice_design,
        "语音",
        "音色设计",
        {"llm": ("根据角色资料生成声音描述", "根据反馈调整声音描述"), "tts": ("根据描述创建自定义音色",)},
    ),
    _task(AiTaskKey.voice_clone, "语音", "声音克隆", {"tts": ("根据参考音频克隆声音",)}),
)
AI_TASK_BY_ID: dict[AiTaskKey, AiTaskDefinition] = {definition.id: definition for definition in AI_TASKS}
