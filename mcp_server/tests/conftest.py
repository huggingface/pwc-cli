from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _cursor_secrets(monkeypatch):
    monkeypatch.setenv("PWC_MCP_CURSOR_KEY_CURRENT", "test-current-cursor-secret")
    monkeypatch.delenv("PWC_MCP_CURSOR_KEY_PREVIOUS", raising=False)
