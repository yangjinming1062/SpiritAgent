"""动作素材：独立的动作语义定义与评审状态。

动作定义不绑定衣柜、环境或成品影像；同一动作可被不同素材组合使用。组合制作时另行判断
具体环境与道具是否满足动作要求。登记定义本身不启动图片或视频制作。
"""

from typing import Any

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class ActionDefinition(ModelBase, TimestampMixin):
    __tablename__ = "action_definitions"
    __table_args__ = (
        UniqueConstraint("user_id", "preset_key", name="uq_action_definitions_preset"),
        CheckConstraint("performance_role IN ('idle', 'sustained', 'reaction')", name="action_performance_role"),
        CheckConstraint("motion_intensity IN ('subtle', 'normal', 'expressive')", name="action_motion_intensity"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 名称只用于展示和检索，不承担跨来源唯一身份；内容不同但名称相同的动作各自独立。
    name: Mapped[str] = mapped_column(String(64))
    normalized_name: Mapped[str] = mapped_column(String(128), index=True)
    motion_description: Mapped[str] = mapped_column(Text)
    use_when_json: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    avoid_when_json: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    # 允许发起本动作的情境类别，如 chat、manual、periodic、user_return。
    allowed_triggers_json: Mapped[list[str]] = mapped_column(
        JSONB,
        default=lambda: ["chat", "manual"],
        server_default=text('\'["chat", "manual"]\''),
    )
    # 播放方式：loop 持续循环，once 为短动作、结束后返回持续画面。
    clip_kind: Mapped[str] = mapped_column(String(8))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    # 结构化语义：调度用标签与表现属性，客户端按词表匹配而不是解读描述文本。
    semantic_tags_json: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    performance_role: Mapped[str] = mapped_column(String(12), default="reaction", server_default=text("'reaction'"))
    motion_intensity: Mapped[str] = mapped_column(String(12), default="normal", server_default=text("'normal'"))
    interruptible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    source: Mapped[str] = mapped_column(String(24))
    preset_key: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default=text("'pending'"), index=True)
    review_reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # 冻结的评审依据：角色身体能力与动作含义；具体环境与道具在组合制作时判断。
    review_source_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
