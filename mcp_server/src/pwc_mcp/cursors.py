from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

CURSOR_SCHEMA_VERSION = 1
CURSOR_LIFETIME_SECONDS = 3600
MAX_CURSOR_BYTES = 2048
MAX_CHUNK_BYTES = 65_536
_CONTENT_VERSION = re.compile(r"[0-9a-f]{64}")
_PAPER_ID = re.compile(r"(?:\d{4}\.\d{4,5}|\d{1,20})")


class CursorReferenceMismatch(ValueError):
    """A valid cursor whose paper reference differs from the one supplied.

    The caller may still honour it when the new reference resolves to the
    cursor's paper (an agent that started from an arXiv ID and continues with
    the numeric catalog ID, say).
    """

    def __init__(self, state: CursorState) -> None:
        super().__init__(
            "invalid continuation cursor: it belongs to another paper reference"
        )
        self.state = state


@dataclass(frozen=True)
class CursorState:
    reference: str
    paper: str
    source: str
    content_version: str
    offset: int
    limit: int
    expires_at: int


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key_id(secret: bytes) -> str:
    return hashlib.sha256(secret).hexdigest()[:16]


class CursorCodec:
    def __init__(
        self,
        current_secret: str,
        *,
        previous_secret: str | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not current_secret:
            raise ValueError("current cursor secret is required")
        current = current_secret.encode()
        self._current_id = _key_id(current)
        self._keys = {self._current_id: current}
        if previous_secret:
            previous = previous_secret.encode()
            self._keys[_key_id(previous)] = previous
        self._now = now

    def encode(self, state: CursorState) -> str:
        now = int(self._now())
        if not self._valid_state(state) or not (
            now < state.expires_at <= now + CURSOR_LIFETIME_SECONDS
        ):
            raise ValueError("invalid continuation cursor")
        payload = {
            "v": CURSOR_SCHEMA_VERSION,
            "ref": state.reference,
            "paper": state.paper,
            "source": state.source,
            "version": state.content_version,
            "offset": state.offset,
            "limit": state.limit,
            "kid": self._current_id,
            "exp": state.expires_at,
        }
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = hmac.digest(self._keys[self._current_id], encoded.encode(), "sha256")
        token = f"{encoded}.{_b64encode(signature)}"
        if len(token) > MAX_CURSOR_BYTES:
            raise ValueError("invalid continuation cursor")
        return token

    def decode(self, token: str, *, reference: str) -> CursorState:
        try:
            if not token or len(token) > MAX_CURSOR_BYTES:
                raise ValueError
            encoded, signature_text = token.split(".", 1)
            raw = _b64decode(encoded)
            if len(raw) > MAX_CURSOR_BYTES:
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError
            key_id = payload.get("kid")
            if not isinstance(key_id, str):
                raise TypeError
            secret = self._keys.get(key_id)
            if secret is None:
                raise ValueError
            supplied = _b64decode(signature_text)
            expected = hmac.digest(secret, encoded.encode(), "sha256")
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            state = CursorState(
                reference=payload["ref"],
                paper=payload["paper"],
                source=payload["source"],
                content_version=payload["version"],
                offset=payload["offset"],
                limit=payload["limit"],
                expires_at=payload["exp"],
            )
            if (
                payload.get("v") != CURSOR_SCHEMA_VERSION
                or set(payload)
                != {
                    "v",
                    "ref",
                    "paper",
                    "source",
                    "version",
                    "offset",
                    "limit",
                    "kid",
                    "exp",
                }
                or not self._valid_state(state)
            ):
                raise ValueError
            if state.expires_at <= int(self._now()):
                raise TimeoutError
            if state.reference != reference.strip():
                raise CursorReferenceMismatch(state)
            return state
        except TimeoutError as error:
            raise ValueError("expired continuation cursor") from error
        except CursorReferenceMismatch:
            raise
        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            binascii.Error,
        ) as error:
            raise ValueError("invalid continuation cursor") from error

    @staticmethod
    def _valid_state(state: CursorState) -> bool:
        return (
            isinstance(state.reference, str)
            and 1 <= len(state.reference) <= 500
            and state.reference == state.reference.strip()
            and isinstance(state.paper, str)
            and _PAPER_ID.fullmatch(state.paper) is not None
            and state.source in {"arxiv", "external"}
            and (state.source == "external") == state.paper.isdigit()
            and isinstance(state.content_version, str)
            and _CONTENT_VERSION.fullmatch(state.content_version) is not None
            and isinstance(state.offset, int)
            and not isinstance(state.offset, bool)
            and 1 <= state.offset <= 2**63 - 1
            and isinstance(state.limit, int)
            and not isinstance(state.limit, bool)
            and 1 <= state.limit <= MAX_CHUNK_BYTES
            and isinstance(state.expires_at, int)
            and not isinstance(state.expires_at, bool)
        )
