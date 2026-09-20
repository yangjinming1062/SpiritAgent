from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 房间 / 外观政策默认值；同步显式字符串以便其他地方引用。
BACKDROP_POLICY_DEFAULT: str = "llm_may_replace"
OUTFIT_POLICY_DEFAULT: str = "llm_may_replace"

if TYPE_CHECKING:
    from modules.auth import User


class CompanionOutfit(ModelBase, TimestampMixin):
    """外观（着装参考）：一套经确认的全身立绘 + LLM 着装描述。
    服装/发型属可换元素而非身份变更，不受形象锁定约束；激活装不可删 ⇒ 衣柜非空后永不回空。
    status 流转：draft → ready | failed | expired；ready 表示参考图就绪，
    不代表任何可播放形象（模型 / 视频）已就绪。"""

    __tablename__ = "companion_outfits"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="新外观")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 全身立绘裸路径（草稿期 temp-media/，确认后 companion-avatars/），读取时重签名
    fullbody_url: Mapped[str] = mapped_column(String(2048), default="")
    # draft → ready | failed | expired
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"), index=True)
    # 审计：用户着装描述 / feedback / 参考图前缀标记，仿 AvatarAsset.prompt_json
    source_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


# 视频动作包必需动作；可选动作（wave/nod 等）允许出现在 manifest 但不阻塞 ready。
REQUIRED_VIDEO_ACTIONS: tuple[str, ...] = ("idle", "walk_left", "walk_right", "drag")


class CompanionVideoPack(ModelBase, TimestampMixin):
    """角色视频动作包（不可变版本）：一个外观版本的一套动作片段 + 描述符 manifest。
    status 流转：processing → ready | failed；ready 仅表示「必需动作全部有效且服务端
    发布完成」，不改变其他形象状态。版本不可覆盖：单动作重做生成新版本行，
    可复用未变化动作的资源哈希；发布与激活只能由 video 编排在用户锁内翻转。"""

    __tablename__ = "companion_video_packs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id"), nullable=True)
    pack_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(16),
        default="processing",
        server_default=text("'processing'"),
        index=True,
    )
    # 描述符（spiritagent.video.pack/1）：身份、画布、动作映射、命中与调度约束；资源为不可变路径+哈希
    manifest_json: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    manifest_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    # 参考版本哈希：换装 / 参考变更后迟到的构建结果凭它被拒，不覆盖新外观的包
    reference_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    # 同一包及其单动作重做版本共用冻结参考字节；context_json 保存生成时角色资料。
    reference_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    context_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompanionVideoJob(ModelBase, TimestampMixin):
    """角色视频动作任务：与聊天媒体 VideoGenJob 分离。status 是任务状态，
    stage 是其在链上的阶段（脚本 / 提交 / 生成 / 下载 / 处理 / 发布），二者分开持久化；
    供应商等待不占数据库长事务。每个动作独立建行，成功动作与源素材可恢复；
    provider_task_id 在提交成功后立即落库，
    进程重启凭它续轮询，不重复提交付费任务。"""

    __tablename__ = "companion_video_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pack_id: Mapped[int | None] = mapped_column(ForeignKey("companion_video_packs.id"), nullable=True)
    outfit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(32), default="idle", server_default=text("'idle'"))
    status: Mapped[str] = mapped_column(
        String(16),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(16), default="submit", server_default=text("'submit'"))
    provider: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    # 提交时钉死的模型名，配置改变后仍按原任务模型查询。
    model: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    provider_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    reference_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    # 按参考生成：单动作的起始姿态与运动描述快照。
    script_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 生成源视频的持久产物路径（companion-assets 裸路径），处理中断后凭它续跑
    artifact_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, default=None)
    pose_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, default=None)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Persona(ModelBase, TimestampMixin):
    """伙伴人设主表：definition_json 存原始字段定义，运行期按 session language 实时渲染。"""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    personality_tags_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    is_portrait_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("FALSE"),
        index=True,
    )
    portrait_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 当前激活的房间图行；None = 尚未生成。
    active_backdrop_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_room_backdrops.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 房间图政策：llm_may_replace（默认，LLM 可主动换房）/ locked（用户锁住，LLM 主动换房被拒）。
    backdrop_policy: Mapped[str] = mapped_column(
        String(16),
        default=BACKDROP_POLICY_DEFAULT,
        server_default=text(f"'{BACKDROP_POLICY_DEFAULT}'"),
    )
    # 外观政策：llm_may_replace（默认，夜间可自主换装 / 添置外观）/ locked（仅用户可操作）。
    outfit_policy: Mapped[str] = mapped_column(
        String(16),
        default=OUTFIT_POLICY_DEFAULT,
        server_default=text(f"'{OUTFIT_POLICY_DEFAULT}'"),
    )
    # 当前心境：由独立状态推理更新的最新心理活动说明，端到端投射至客户端身份轨展示。
    current_mood: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    user: Mapped["User"] = relationship(back_populates="persona")


class AvatarAsset(ModelBase):
    """asset_url 存在 companion-avatars/（持久）以让重新登录跨过 24h temp-media TTL。"""

    __tablename__ = "avatar_assets"
    # 部分唯一索引（每用户一个 active）位于 alembic baseline——需要 WHERE 子句。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    # 日常出镜的全身参考；锁定身份后仍可重绘。
    seed_fullbody_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    # 确认形象的外观参考正面立绘（视频链的身份锚）：onboarding 确认后锁定身份；
    # 外观草稿与视频链以它为身份参考。
    reference_image_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")
