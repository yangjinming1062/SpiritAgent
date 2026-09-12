from components import session_scope
from modules.channels import ChannelBinding
from modules.ws import emit_ws_event


async def update_binding_status(
    binding_id: int,
    status: str,
    *,
    error: str | None = None,
    account_ref: str | None = None,
    account_name: str | None = None,
) -> None:
    """绑定状态迁移的单一入口（manager 守卫循环与适配器登录/过期路径共用）：
    落库 + 状态实际变化时写 ``channel.status`` outbox 事件（桌面离线暂存重投）；
    账号标识仅在首次拿到时回填（wechat 登录确认 / 后续渠道连接成功）。
    """
    async with session_scope() as db:
        row = await db.get(ChannelBinding, binding_id)
        if row is None:
            return
        changed = row.status != status
        row.status = status
        row.last_error = error if status == "error" else None
        if account_ref and not row.account_ref:
            row.account_ref = account_ref
        if account_name and not row.account_name:
            row.account_name = account_name
        if changed:
            emit_ws_event(
                db,
                user_id=row.user_id,
                event_type="channel.status",
                payload={
                    "channel": row.channel,
                    "status": status,
                    **({"account_name": row.account_name} if row.account_name else {}),
                    **({"error": error} if error else {}),
                },
            )
        await db.commit()
