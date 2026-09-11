from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class ChannelBinding(ModelBase, TimestampMixin):
    """用户 ↔ 外部 IM 渠道的绑定行：每 (user, channel) 一条（uq 约束），持有该渠道专属 im 会话的锚点。

    conversation_id 唯一外键把「每渠道一条对话」钉在 DB 层——binding 的 (user_id, channel) 唯一性传递为
    「每用户每渠道至多一条 im 会话」，UNIQUE 又阻止两条绑定共享同一会话。凭据/配置走 Text JSON，
    与 user_model_configs 同一落盘风格（明文，REST 永不回显）。
    """

    __tablename__ = "channel_bindings"
    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_channel_bindings_user_channel"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="disabled", server_default=text("'disabled'"), index=True)
    # 渠道专属 im 会话锚点；会话被删除时置 NULL（绑定存活，下次消息重新开会话）。
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    # 渠道侧配置；适配器解析，schema 无约束以便加渠道不动迁移。
    config_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    # 渠道凭据（微信: bot_token/baseurl/context_tokens/typing_ticket）；REST 永不回显。
    credentials: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    account_ref: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    account_name: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChannelPeer(ModelBase, TimestampMixin):
    """绑定下的对端（如微信 wxid）：默认拒绝 + 配对审批的访问控制载体。"""

    __tablename__ = "channel_peers"
    __table_args__ = (UniqueConstraint("binding_id", "peer_id", name="uq_channel_peers_binding_peer"),)

    binding_id: Mapped[int] = mapped_column(ForeignKey("channel_bindings.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[str] = mapped_column(String(128))
    peer_name: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"), index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
