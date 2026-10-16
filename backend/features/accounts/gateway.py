"""一次性网关准入票据；登录身份在签发和握手时分别核对。"""

import hashlib
import secrets
import time
from dataclasses import dataclass

from pydantic import BaseModel
from runtime.client_context import ChatRequestClientContext

TICKET_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class GatewayIdentity:
    user_id: int
    login_record_id: int | None = None
    device_id: int | None = None
    client_context: ChatRequestClientContext | None = None


class GatewayTicketResponse(BaseModel):
    ticket: str
    expires_in: int


_tickets: dict[str, tuple[GatewayIdentity, float]] = {}


def issue_gateway_ticket(identity: GatewayIdentity) -> GatewayTicketResponse:
    if (identity.login_record_id is None) == (identity.device_id is None):
        raise ValueError("Gateway identity must belong to one login or remote device")
    now = time.monotonic()
    for key in [key for key, (_, expiry) in _tickets.items() if expiry <= now]:
        del _tickets[key]
    ticket = secrets.token_urlsafe(32)
    key = hashlib.sha256(ticket.encode()).hexdigest()
    _tickets[key] = (identity, now + TICKET_TTL_SECONDS)
    return GatewayTicketResponse(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


def consume_gateway_ticket(ticket: str) -> GatewayIdentity | None:
    if len(ticket) != 43 or not ticket.isascii():
        return None
    key = hashlib.sha256(ticket.encode()).hexdigest()
    pending = _tickets.pop(key, None)
    if pending is None or pending[1] <= time.monotonic():
        return None
    return pending[0]
