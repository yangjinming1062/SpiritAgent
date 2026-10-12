"""产品模型调用用途；能力、管理页分组及输入要求共用此目录。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .ai_models import ModelModality


class AiFeature(StrEnum):
    companion_chat = "companion_chat"
    work_chat = "work_chat"
    delegated_task = "delegated_task"
    scheduled_task = "scheduled_task"
    proactive_companion = "proactive_companion"
    conversation_title = "conversation_title"
    context_compression = "context_compression"
    web_summary = "web_summary"
    runner_completion = "runner_completion"
    mood_update = "mood_update"
    personality_tags = "personality_tags"
    spatial_decision = "spatial_decision"
    idle_expression = "idle_expression"
    desktop_idle_expression = "desktop_idle_expression"
    memory_proposal = "memory_proposal"
    memory_review = "memory_review"
    memory_embedding = "memory_embedding"
    nightly_memory = "nightly_memory"
    nightly_planning = "nightly_planning"
    nightly_reflection = "nightly_reflection"
    nightly_diary = "nightly_diary"
    post_plan = "post_plan"
    post_reply = "post_reply"
    post_image = "post_image"
    post_video = "post_video"
    post_voice = "post_voice"
    avatar_prompt = "avatar_prompt"
    avatar_image = "avatar_image"
    fullbody_guidance = "fullbody_guidance"
    fullbody_image = "fullbody_image"
    character_card = "character_card"
    garment_analysis = "garment_analysis"
    outfit_guidance = "outfit_guidance"
    outfit_image = "outfit_image"
    outfit_description = "outfit_description"
    scene_image = "scene_image"
    scene_review = "scene_review"
    scene_description = "scene_description"
    action_proposal = "action_proposal"
    action_image_description = "action_image_description"
    action_script = "action_script"
    action_alignment_image = "action_alignment_image"
    action_pose_image = "action_pose_image"
    action_video = "action_video"
    action_review = "action_review"
    peek_geometry = "peek_geometry"
    desktop_action_proposal = "desktop_action_proposal"
    desktop_action_design = "desktop_action_design"
    desktop_pose_image = "desktop_pose_image"
    desktop_video = "desktop_video"
    desktop_video_review = "desktop_video_review"
    chat_image = "chat_image"
    chat_image_inspection = "chat_image_inspection"
    chat_video = "chat_video"
    image_identity = "image_identity"
    video_identity = "video_identity"
    speech_input = "speech_input"
    companion_voice = "companion_voice"
    speech_preview = "speech_preview"
    voice_design = "voice_design"
    moderation_rewrite = "moderation_rewrite"


class AiFeatureDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AiFeature
    group: str
    label: str
    service: str
    required_input_modalities: frozenset[ModelModality] = frozenset({ModelModality.text})
    fast_decision: bool = False


def _feature(
    feature: AiFeature,
    group: str,
    label: str,
    service: str = "llm",
    *,
    image_input: bool = False,
    fast_decision: bool = False,
) -> AiFeatureDefinition:
    return AiFeatureDefinition(
        id=feature,
        group=group,
        label=label,
        service=service,
        required_input_modalities=frozenset(
            {ModelModality.audio}
            if service == "stt"
            else {ModelModality.text, ModelModality.image}
            if image_input
            else {ModelModality.text},
        ),
        fast_decision=fast_decision,
    )


AI_FEATURES: tuple[AiFeatureDefinition, ...] = (
    _feature(AiFeature.companion_chat, "聊天", "陪伴聊天"),
    _feature(AiFeature.work_chat, "聊天", "工作与专业预设聊天"),
    _feature(AiFeature.delegated_task, "聊天", "子 Agent 委派"),
    _feature(AiFeature.scheduled_task, "聊天", "普通定时任务"),
    _feature(AiFeature.conversation_title, "聊天", "会话标题"),
    _feature(AiFeature.context_compression, "聊天", "上下文压缩"),
    _feature(AiFeature.web_summary, "聊天", "网页内容摘要"),
    _feature(AiFeature.runner_completion, "聊天", "Runner 代理补全"),
    _feature(AiFeature.chat_image, "聊天媒体", "聊天图片与重做", "image_gen"),
    _feature(AiFeature.chat_image_inspection, "聊天媒体", "聊天验图", image_input=True),
    _feature(AiFeature.chat_video, "聊天媒体", "聊天视频", "video_gen"),
    _feature(AiFeature.speech_input, "语音", "语音输入转写", "stt"),
    _feature(AiFeature.companion_voice, "语音", "伙伴回复合成", "tts"),
    _feature(AiFeature.speech_preview, "语音", "朗读与试听", "tts"),
    _feature(AiFeature.voice_design, "语音", "自定义音色设计", "tts"),
    _feature(AiFeature.proactive_companion, "主动陪伴", "主动陪伴回合"),
    _feature(AiFeature.mood_update, "主动陪伴", "心情更新"),
    _feature(AiFeature.personality_tags, "主动陪伴", "性格标签提取"),
    _feature(AiFeature.spatial_decision, "主动陪伴", "自主移动判断", fast_decision=True),
    _feature(AiFeature.idle_expression, "主动陪伴", "窗口空闲动作选择", fast_decision=True),
    _feature(AiFeature.desktop_idle_expression, "主动陪伴", "桌面生活动作选择", fast_decision=True),
    _feature(AiFeature.memory_proposal, "记忆", "即时记忆提案审核"),
    _feature(AiFeature.memory_review, "记忆", "回合后与定期记忆审阅"),
    _feature(AiFeature.memory_embedding, "记忆", "记忆入库与查询向量", "embedding"),
    _feature(AiFeature.nightly_memory, "夜间", "夜间记忆整理"),
    _feature(AiFeature.nightly_planning, "夜间", "自主活动规划"),
    _feature(AiFeature.nightly_reflection, "夜间", "相处理解反思"),
    _feature(AiFeature.nightly_diary, "夜间", "日记决定与创作"),
    _feature(AiFeature.post_plan, "动态", "发布决定与内容计划"),
    _feature(AiFeature.post_reply, "动态", "评论回复"),
    _feature(AiFeature.post_image, "动态", "动态图片", "image_gen"),
    _feature(AiFeature.post_video, "动态", "动态视频", "video_gen"),
    _feature(AiFeature.post_voice, "动态", "语音动态与旁白", "tts"),
    _feature(AiFeature.avatar_prompt, "形象与衣柜", "头像提示词整理"),
    _feature(AiFeature.avatar_image, "形象与衣柜", "头像生成与重绘", "image_gen"),
    _feature(AiFeature.fullbody_guidance, "形象与衣柜", "身体与姿态指导", image_input=True),
    _feature(AiFeature.fullbody_image, "形象与衣柜", "全身生成与重绘", "image_gen"),
    _feature(AiFeature.character_card, "形象与衣柜", "角色卡视觉特征提取", image_input=True),
    _feature(AiFeature.garment_analysis, "形象与衣柜", "服装参考解析", image_input=True),
    _feature(AiFeature.outfit_guidance, "形象与衣柜", "换装身体与姿态指导", image_input=True),
    _feature(AiFeature.outfit_image, "形象与衣柜", "换装生成与微调", "image_gen"),
    _feature(AiFeature.outfit_description, "形象与衣柜", "造型命名与描述"),
    _feature(AiFeature.scene_image, "场景", "场景图片生成与重绘", "image_gen"),
    _feature(AiFeature.scene_review, "场景", "场景重复伙伴检查", image_input=True),
    _feature(AiFeature.scene_description, "场景", "场景标题与描述", image_input=True),
    _feature(AiFeature.action_proposal, "窗口动作", "动作提案评审", image_input=True),
    _feature(AiFeature.action_image_description, "窗口动作", "静态动作描述", image_input=True),
    _feature(AiFeature.action_script, "窗口动作", "视频动作脚本", image_input=True),
    _feature(AiFeature.action_alignment_image, "窗口动作", "外观参考校准图", "image_gen"),
    _feature(AiFeature.action_pose_image, "窗口动作", "动作姿态图", "image_gen"),
    _feature(AiFeature.action_video, "窗口动作", "动作视频", "video_gen"),
    _feature(AiFeature.action_review, "窗口动作", "动作成品复核", image_input=True),
    _feature(AiFeature.peek_geometry, "窗口动作", "探身遮挡位置分析", image_input=True),
    _feature(AiFeature.desktop_action_proposal, "桌面生活", "桌面动作提案评审", image_input=True),
    _feature(AiFeature.desktop_action_design, "桌面生活", "生活构图与运动设计", image_input=True),
    _feature(AiFeature.desktop_pose_image, "桌面生活", "生活起始画面", "image_gen"),
    _feature(AiFeature.desktop_video, "桌面生活", "生活视频", "video_gen"),
    _feature(AiFeature.desktop_video_review, "桌面生活", "生活视频成品检查", image_input=True),
    _feature(AiFeature.image_identity, "质量与恢复", "图片身份评分", image_input=True),
    _feature(AiFeature.video_identity, "质量与恢复", "视频抽帧身份评分", image_input=True),
    _feature(AiFeature.moderation_rewrite, "质量与恢复", "图片提示词审核改写"),
)
AI_FEATURE_BY_ID: dict[AiFeature, AiFeatureDefinition] = {definition.id: definition for definition in AI_FEATURES}
