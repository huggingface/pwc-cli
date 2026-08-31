from __future__ import annotations

import base64
import binascii
import json


def encode_cursor(paper: str, offset: int) -> str:
    payload = json.dumps(
        {"paper": paper, "offset": offset}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str, paper: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if payload.get("paper") != paper:
            raise ValueError("cursor belongs to another paper")
        offset = payload.get("offset")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 1:
            raise ValueError("cursor offset is invalid")
        return offset
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as error:
        raise ValueError("invalid continuation cursor") from error
