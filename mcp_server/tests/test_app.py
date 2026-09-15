from __future__ import annotations

import logging

from pwc_mcp.app import create_app
from starlette.testclient import TestClient
from test_server import StubCatalog


def test_health_and_browser_origin_policy_are_explicit():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        allowed_origins=["https://chatgpt.com"],
    )

    with TestClient(app) as client:
        health = client.get("/health")
        rejected = client.post(
            "/mcp",
            headers={"Origin": "https://attacker.example"},
            json={},
        )
        preflight = client.options(
            "/mcp",
            headers={
                "Origin": "https://chatgpt.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,mcp-protocol-version,mcp-method,mcp-name",
            },
        )

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "pwc-mcp",
        "version": "0.1.0",
        "protocol": "2026-07-28",
    }
    assert rejected.status_code == 403
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://chatgpt.com"


def test_wildcard_browser_origin_is_rejected_at_startup():
    try:
        create_app(StubCatalog(), allowed_origins=["*"])
    except ValueError as error:
        assert "must not contain a wildcard" in str(error)
    else:
        raise AssertionError("wildcard Origin was accepted")


def test_rate_limit_is_content_free_and_returns_retry_metadata():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        request_limit=1,
    )

    with TestClient(app) as client:
        first = client.post("/mcp", json={})
        limited = client.post("/mcp", json={})

    assert first.status_code != 429
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json() == {"error": "rate_limit_exceeded"}


def test_semantic_limit_uses_body_tool_name_when_header_disagrees():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        request_limit=10,
        semantic_limit=1,
    )
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search_papers",
            "arguments": {"query": "attention", "mode": "semantic"},
        },
    }

    with TestClient(app) as client:
        first = client.post("/mcp", json=request, headers={"MCP-Name": "get_task"})
        limited = client.post("/mcp", json=request, headers={"MCP-Name": "get_task"})

    assert first.status_code != 429
    assert limited.status_code == 429


def test_request_body_limit_is_enforced_before_protocol_parsing():
    app = create_app(StubCatalog(), allowed_hosts=["testserver"])

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            content=b"x" * (2 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"error": "request_too_large"}


def test_operational_telemetry_normalizes_untrusted_labels(caplog):
    secret = "private unreleased project heliotrope"
    app = create_app(StubCatalog(), allowed_hosts=["testserver"])

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": secret, "arguments": {}},
            },
            headers={
                "MCP-Name": secret,
                "MCP-Protocol-Version": secret,
            },
        )

    assert secret not in caplog.text
    assert "tool=unknown" in caplog.text
    assert "protocol=unknown" in caplog.text


def test_one_http_endpoint_serves_modern_and_legacy_protocol_eras():
    app = create_app(StubCatalog(), allowed_hosts=["testserver"])
    modern_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }
    legacy_body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }

    with TestClient(app) as client:
        modern = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "server/discover",
            },
            json=modern_body,
        )
        legacy = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json=legacy_body,
        )

    assert modern.status_code == 200
    assert modern.json()["result"]["supportedVersions"] == ["2026-07-28"]
    assert modern.json()["result"]["cacheScope"] == "public"
    assert legacy.status_code == 200
    assert legacy.json()["result"]["protocolVersion"] == "2025-11-25"
