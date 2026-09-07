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

# 房间政策默认值；同步显式字符串以便其他地方引用。Persona.backdrop_policy 字符串与该字面量同源。
BACKDROP_POLICY_DEFAULT: str = "llm_may_replace"

if TYPE_CHECKING:
    from modules.auth import User


class Companion3DModel(ModelBase, TimestampMixin):
    """供应商生成的 3D 模型；status 流转：generating → pending_download → downloading → succeeded | failed；下载阶段任意失败 → download_failed（可通过 ``companion.model.retryDownload`` 重试，付费结果保存在 provider_task_id + download_urls_json 中）。"""

    __tablename__ = "companion_3d_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_url: Mapped[str] = mapped_column(Text, default="")
    source_portrait_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="base_texture")
    species: Mapped[str] = mapped_column(String(64), default="人类", server_default=text("'人类'"))
    rig_type: Mapped[str] = mapped_column(String(32), default="biped", server_default=text("'biped'"), index=True)
    rig_naming: Mapped[str] = mapped_column(String(16), default="tripo", server_default=text("'tripo'"))
    # 模型生成所用的 seed 图风格（anime_game_cg | realistic）—— 路由客户端 NPR/PBR 渲染风格；旧行默认 realistic 以保留 PBR 外观。
    style: Mapped[str] = mapped_column(String(16), default="realistic", server_default=text("'realistic'"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    has_rig: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    # 供应商声明的「语义键 → 烘焙进 GLB 的 clip 名」；空字典即该产物不含动画。
    clip_map_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    # 当前 provider_task_id 在链上的阶段：submit / rig / animate —— 用于进程崩溃接续时判断"该 task_id 指向的产物是不是最终含动画的 GLB"，避免把未完成链中的中间产物当终产物落盘。
    provider_phase: Mapped[str] = mapped_column(String(16), default="submit", server_default=text("'submit'"))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    # 付费结果恢复句柄：生成完成瞬间、下载开始前写入，保证下载失败也不丢已计费资产；provider_task_id 是「再次查问即得 URL」的 id（云端 rigged 用 rig task id，其他用 submit id）。
    provider_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    download_urls_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class CompanionExpression(ModelBase, TimestampMixin):
    """自定义情绪注册表：LLM 创建的情绪 token，可用作 [affect:NAME]；本表登记 token 与 clip 匹配 / 展示元数据。"""

    __tablename__ = "companion_expressions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(32))
    valence: Mapped[str] = mapped_column(String(16), default="neutral")
    description: Mapped[str] = mapped_column(Text, default="")
    # 可选单个 emoji 图标，聊天坞站 label 旁展示。
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")


class CompanionOutfit(ModelBase, TimestampMixin):
    """2D 换装外观：一套全身立绘 + 对应 2d 切分行 + LLM 着装描述。
    服装/发型属可换元素而非身份变更，不受形象锁定约束；激活装不可删 ⇒ 衣柜非空后永不回空。
    partial unique（每用户一个 active / 一个 splitting）只存在于 baseline 迁移，不进模型 metadata。"""

    __tablename__ = "companion_outfits"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="新外观")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 全身立绘裸路径（草稿期 temp-media/，确认后 companion-avatars/），读取时重签名
    fullbody_url: Mapped[str] = mapped_column(String(2048), default="")
    style: Mapped[str] = mapped_column(String(32), default="cel_shading", server_default=text("'cel_shading'"))
    # draft → splitting → ready | failed | expired
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"), index=True)
    # 审计：用户着装描述 / feedback / 参考图前缀标记，仿 AvatarAsset.prompt_json
    source_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    # 确认即穿着：切分成功后自动翻转激活；期间手动穿着其他装会清掉该标记，切分完成只入柜不换装
    pending_wear: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))


class Companion2DModel(ModelBase, TimestampMixin):
    """see-through 拆分产物：manifest 为 PSD 木偶描述符（spiritagent.2d.psd/1），layers 指向分层 PSD 资产；客户端 puppet 渲染层消费。
    partial unique（每用户一条 active）只存在于 baseline 迁移，不进模型 metadata。"""

    __tablename__ = "companion_2d_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outfit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    style: Mapped[str] = mapped_column(String(32), default="cel_shading", server_default=text("'cel_shading'"))
    status: Mapped[str] = mapped_column(String(16), default="generating", server_default=text("'generating'"), index=True)
    manifest_json: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    manifest_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    layers_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Persona(ModelBase, TimestampMixin):
    """伙伴人设主表：definition_json 存原始字段定义，运行期按 session language 实时渲染。"""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    personality_tags_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    is_portrait_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    portrait_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 渲染模式：2d（默认 PSD 木偶动画版）或 3d（云端 GLB 模型）；3d 失败自动回退到 2d。
    render_mode: Mapped[str] = mapped_column(String(8), default="2d", server_default=text("'2d'"), index=True)
    # 当前激活的房间图行；None = 尚未生成。
    active_backdrop_id: Mapped[int | None] = mapped_column(ForeignKey("companion_room_backdrops.id", ondelete="SET NULL"), nullable=True)
    # 房间图政策：llm_may_replace（默认，LLM 可主动换房）/ locked（用户锁住，LLM 主动换房被拒）。
    backdrop_policy: Mapped[str] = mapped_column(String(16), default=BACKDROP_POLICY_DEFAULT, server_default=text(f"'{BACKDROP_POLICY_DEFAULT}'"))
    # 当前心境：由 LLM 情绪/情境推理驱动的最新心理活动说明，端到端投射至客户端展示。
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
    seed_front_2d_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    # 3D 建模专用正面种子（A-pose、3D 画风），切 3D 时以 2D 正面种子派生；不覆盖 2D 正面种子（衣柜与 2D 拆分的身份锚）
    seed_front_3d_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_back_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")
