from runtime.events.emit import emit_ws_event
from runtime.events.models import WSEvent

COMPANION_TURN_EVENT = "companion.turn.request"
DEFERRED_REPLY_EVENT = "companion.reply.deferred"
LIFE_CONTACT_EVENT = "companion.life.contact"
COMPANION_TURN_EVENTS = (COMPANION_TURN_EVENT, DEFERRED_REPLY_EVENT, LIFE_CONTACT_EVENT)

__all__ = [
    "COMPANION_TURN_EVENT",
    "COMPANION_TURN_EVENTS",
    "DEFERRED_REPLY_EVENT",
    "LIFE_CONTACT_EVENT",
    "WSEvent",
    "emit_ws_event",
]
