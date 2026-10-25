"""时间戳与 BIGINT 身份组成的稳定降序游标。"""

import base64
import json
from datetime import datetime

from .api_ids import parse_resource_id


def decode_timestamp_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if cursor is None:
        return None
    try:
        if not cursor or len(cursor) > 512:
            raise ValueError("Invalid cursor")
        values = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(values, list) or len(values) != 2 or not isinstance(values[0], str):
            raise ValueError("Invalid cursor")
        timestamp = datetime.fromisoformat(values[0])
        if timestamp.tzinfo is None:
            raise ValueError("Cursor timestamp requires a timezone")
        return timestamp, parse_resource_id(values[1])
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid cursor") from exc


def encode_timestamp_cursor(timestamp: datetime, identifier: int) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("Cursor timestamp requires a timezone")
    payload = json.dumps([timestamp.isoformat(), str(identifier)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode()
