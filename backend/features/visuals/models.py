"""影像库：组合成品、制作工作项与呈现播放事实。

影像成品只收录专门用于伙伴呈现的组合成品（图片或视频）；聊天配图、动态媒体和素材参考图不进入本模块。
组合快照冻结伙伴身份与衣柜、环境、动作三份素材的引用及描述，素材删除后快照与文件引用继续有效。
"""

from datetime import datetime

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column


class VisualItem(ModelBase, TimestampMixin):
    """一份可呈现影像：图片或视频，重做成功后在同一行替换成品版本。"""

    __tablename__ = "visual_items"
    __table_args__ = (
        # 同账户重复引用同一张生成图片只产生一条图片记录。
        Index(
            "uq_visual_items_image_content",
            "user_id",
            "content_hash",
            unique=True,
            postgresql_where=text("media_kind = 'image'"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_kind: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(160), default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(24), default="ready", server_default=text("'ready'"))
    # 当前成品版本的不可变媒体路径；旧版本文件由统一回收在无引用后删除。
    media_path: Mapped[str] = mapped_column(Text)
    # 技术派生封面，不单独成为影像。
    poster_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    # 实际画面描述；素材来源描述在组合快照中冻结。
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    outfit_description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    snapshot_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    outfit_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_outfits.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_scenes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_definitions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 视频的真实输入图：逻辑链接可随后续重做置空，路径与哈希是冻结依赖。
    input_image_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    input_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VisualJob(ModelBase, TimestampMixin):
    """制作工作项：新建或重做一份影像，与当前成品分离。"""

    __tablename__ = "visual_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 重做目标；空表示新建。等待、失败、暂停和取消均保留目标成品。
    target_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(16), default="create", server_default=text("'create'"))
    target_kind: Mapped[str] = mapped_column(String(8), default="video", server_default=text("'video'"))
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 冻结输入：组合快照（身份、三份素材、计划、目标画幅与生成要求）。
    snapshot_json: Mapped[str] = mapped_column(Text)
    feedback: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    video_feedback: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    state: Mapped[str] = mapped_column(String(24), default="queued", server_default=text("'queued'"), index=True)
    stage: Mapped[str] = mapped_column(String(24), default="prepare", server_default=text("'prepare'"))
    # 供应商进度与制作阶段状态；格式由 visuals.schemas.VisualJobProgress 定义。
    progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 可核验候选；未消费的供应商结果不得恢复为活动提交。
    candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    repair_limit: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 目标包含视频时，合格起始图也独立入库。
    result_image_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="SET NULL"),
        nullable=True,
    )


class VisualPresentation(ModelBase, TimestampMixin):
    """每账户一份呈现状态：当前影像、持续影像与播放代次。"""

    __tablename__ = "visual_presentations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    current_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 短动作播放期间保留的有效持续影像。
    loop_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    autonomous_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    # 本机呈现上下文的目标画幅（像素），组合制作以此冻结目标。
    target_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    presentation_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    state_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 待执行播放指令；换号、模式切换和过期后重新校验。
    pending_command_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    loop_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class VisualPlayback(ModelBase, TimestampMixin):
    """播放账本：指令受理、桌面认领与实际回执。"""

    __tablename__ = "visual_playbacks"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    visual_id: Mapped[int] = mapped_column(
        ForeignKey("visual_items.id", ondelete="CASCADE"),
        index=True,
    )
    visual_version: Mapped[int] = mapped_column(Integer)
    play_id: Mapped[str] = mapped_column(String(36), unique=True)
    presentation_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    state_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default=text("'queued'"))
    client_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 实际回执类型：完整图片、视频或窗口静态投影；静态投影不冒充动作执行。
    receipt_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
