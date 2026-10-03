from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .schemas import ChannelTurnSource


class ChannelBinding(ModelBase, TimestampMixin):
    """用户 ↔ 外部 IM 渠道绑定，每 (user, channel) 一条；conversation_id 唯一外键钉住「每用户每渠道至多一条 im 会话」。凭据 Text JSON，REST 永不回显。"""

    __tablename__ = "channel_bindings"
    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_channel_bindings_user_channel"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="disabled", server_default=text("'disabled'"), index=True)
    # 渠道专属 im 会话锚点；会话删除时置 NULL（绑定存活，下次消息重开）。
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
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
    authorization_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    async def authorizes(
        cls,
        db: AsyncSession,
        user_id: int,
        source: ChannelTurnSource,
        *,
        lock: bool = False,
    ) -> bool:
        statement = (
            select(cls.id)
            .join(ChannelBinding, ChannelBinding.id == cls.binding_id)
            .where(
                ChannelBinding.user_id == user_id,
                cls.binding_id == source.binding_id,
                cls.id == source.peer_record_id,
                cls.peer_id == source.peer_id,
                cls.status == "allowed",
                cls.authorization_revision == source.authorization_revision,
            )
        )
        if lock:
            statement = statement.with_for_update(of=cls)
        return await db.scalar(statement) is not None


class ChannelDelivery(ModelBase, TimestampMixin):
    """渠道待补发记录；执行与投递独立，恢复语义见 PROTOCOL「IM 通道」。"""

    __tablename__ = "channel_deliveries"

    binding_id: Mapped[int] = mapped_column(ForeignKey("channel_bindings.id", ondelete="CASCADE"), index=True)
    # 旧版空串没有可证明的接收对端，补发入口将其作废。
    peer_id: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    # ChannelDeliveryPayload 的 JSON；media URL 为裸资产路径。
    payload_json: Mapped[str] = mapped_column(Text)
    # pending / sent / abandoned。
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"), index=True)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
