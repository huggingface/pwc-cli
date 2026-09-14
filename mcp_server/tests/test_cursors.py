from __future__ import annotations

import pytest
from pwc_mcp.cursors import CursorCodec, CursorState


def _state(*, expires_at: int = 4600) -> CursorState:
    return CursorState(
        reference="Attention Is All You Need",
        paper="1706.03762",
        source="arxiv",
        content_version="a" * 64,
        offset=65536,
        limit=65536,
        expires_at=expires_at,
    )


def test_cursor_round_trip_and_replay_are_stable():
    codec = CursorCodec("current-secret", now=lambda: 1000)
    token = codec.encode(_state())

    assert codec.decode(token, reference="Attention Is All You Need") == _state()
    assert codec.decode(token, reference="Attention Is All You Need") == _state()


def test_cursor_rejects_tampering_another_reference_and_expiry():
    codec = CursorCodec("current-secret", now=lambda: 1000)
    token = codec.encode(_state(expires_at=1001))

    with pytest.raises(ValueError, match="invalid continuation cursor"):
        codec.decode(token[:-1] + ("A" if token[-1] != "A" else "B"), reference="Attention Is All You Need")
    with pytest.raises(ValueError, match="invalid continuation cursor"):
        codec.decode(token, reference="Another paper")

    expired = CursorCodec("current-secret", now=lambda: 1002)
    with pytest.raises(ValueError, match="expired continuation cursor"):
        expired.decode(token, reference="Attention Is All You Need")


def test_cursor_rotation_accepts_previous_key_without_extending_expiry():
    old = CursorCodec("old-secret", now=lambda: 1000)
    token = old.encode(_state(expires_at=1200))
    rotated = CursorCodec(
        "new-secret", previous_secret="old-secret", now=lambda: 1100
    )

    assert rotated.decode(token, reference="Attention Is All You Need").expires_at == 1200


def test_cursor_rejects_oversized_or_invalid_state():
    codec = CursorCodec("current-secret", now=lambda: 1000)

    with pytest.raises(ValueError, match="invalid continuation cursor"):
        codec.decode("x" * 4096, reference="paper")
    with pytest.raises(ValueError, match="invalid continuation cursor"):
        codec.encode(_state(expires_at=1000))
    with pytest.raises(ValueError, match="cursor secret"):
        CursorCodec("")
