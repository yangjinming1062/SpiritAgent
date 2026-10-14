from runtime.events.emit import emit_ws_event
from runtime.events.models import WSEvent

COMPANION_TURN_EVENT = "companion.turn.request"

__all__ = ["COMPANION_TURN_EVENT", "WSEvent", "emit_ws_event"]
