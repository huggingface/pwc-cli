from __future__ import annotations

import logging

from pwc_mcp.app import (
    MAX_RESPONSE_BODY_SIZE,
    ResponseSizeLimitMiddleware,
    _client_address,
    create_app,
)
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.routing import Route
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
        "version": "0.2.3",
        "protocol": "2025-11-25",
    }
    assert rejected.status_code == 403
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://chatgpt.com"


def test_discovery_schema_and_bare_get_are_bounded():
    app = create_app(StubCatalog(), allowed_hosts=["testserver"])

    with TestClient(app) as client:
        discovery = client.get("/.well-known/mcp")
        schema = client.get("/docs")
        bare_get = client.get("/mcp")

    assert discovery.status_code == 200
    assert discovery.json()["protocol_version"] == "2025-11-25"
    assert discovery.json()["documentation_url"].endswith("/mcp/schema")
    assert len(schema.json()["tools"]) == 21
    assert bare_get.status_code == 405
    assert bare_get.headers["allow"] == "POST"


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


def test_global_saturation_returns_retry_metadata():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        global_concurrency_limit=0,
    )

    with TestClient(app) as client:
        response = client.post("/mcp", json={})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"error": "server_saturated"}


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


def test_proxy_identity_trusts_only_an_exact_loopback_peer():
    headers = Headers({"x-forwarded-for": "203.0.113.9"})

    assert (
        _client_address({"client": ("127.0.0.1", 1234)}, headers, True) == "203.0.113.9"
    )
    assert _client_address({"client": ("::1", 1234)}, headers, True) == "203.0.113.9"
    assert _client_address({"client": ("10.0.0.2", 1234)}, headers, True) == "10.0.0.2"
    assert (
        _client_address({"client": ("192.168.1.2", 1234)}, headers, True)
        == "192.168.1.2"
    )


def test_loopback_first_party_clients_may_name_their_rate_limit_identity():
    tagged = Headers({"x-pwc-mcp-client": "chat-0123abcd"})
    assert (
        _client_address({"client": ("127.0.0.1", 1)}, tagged, True)
        == "client:chat-0123abcd"
    )
    assert (
        _client_address({"client": ("::1", 1)}, tagged, True) == "client:chat-0123abcd"
    )
    # Proxied traffic always carries X-Forwarded-For, which wins over the tag.
    proxied = Headers(
        {"x-pwc-mcp-client": "chat-0123abcd", "x-forwarded-for": "203.0.113.9"}
    )
    assert _client_address({"client": ("127.0.0.1", 1)}, proxied, True) == "203.0.113.9"
    # Remote peers, disabled trust, and malformed tags fall back to the address.
    assert _client_address({"client": ("10.0.0.2", 1)}, tagged, True) == "10.0.0.2"
    assert _client_address({"client": ("127.0.0.1", 1)}, tagged, False) == "127.0.0.1"
    bad = Headers({"x-pwc-mcp-client": "spaces are/not ok"})
    assert _client_address({"client": ("127.0.0.1", 1)}, bad, True) == "127.0.0.1"
    long = Headers({"x-pwc-mcp-client": "a" * 129})
    assert _client_address({"client": ("127.0.0.1", 1)}, long, True) == "127.0.0.1"


def test_serialized_mcp_response_limit_fails_closed():
    async def oversized(_request):
        return Response(b"x" * (MAX_RESPONSE_BODY_SIZE + 1))

    app = ResponseSizeLimitMiddleware(
        Starlette(routes=[Route("/mcp", oversized, methods=["POST"])])
    )
    with TestClient(app) as client:
        response = client.post("/mcp")

    assert response.status_code == 503
    assert response.json() == {"error": "response_too_large"}


def test_hybrid_search_counts_toward_the_semantic_limit():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        semantic_limit=1,
    )
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search_papers",
            "arguments": {"query": "attention", "mode": "hybrid"},
        },
    }

    with TestClient(app) as client:
        first = client.post("/mcp", json=body)
        limited = client.post("/mcp", json=body)

    assert first.status_code != 429
    assert limited.status_code == 429


def test_omitted_search_mode_defaults_to_hybrid_and_counts_toward_the_semantic_limit():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        semantic_limit=1,
    )
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search_papers", "arguments": {"query": "attention"}},
    }

    with TestClient(app) as client:
        first = client.post("/mcp", json=body)
        limited = client.post("/mcp", json=body)

    assert first.status_code != 429
    assert limited.status_code == 429


def test_keyword_search_does_not_count_toward_the_semantic_limit():
    app = create_app(
        StubCatalog(),
        allowed_hosts=["testserver"],
        semantic_limit=1,
    )
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search_papers",
            "arguments": {"query": "attention", "mode": "keyword"},
        },
    }

    with TestClient(app) as client:
        first = client.post("/mcp", json=body)
        second = client.post("/mcp", json=body)

    assert first.status_code != 429
    assert second.status_code != 429


def test_rate_limits_are_env_configurable_with_hosted_defaults(monkeypatch):
    from pwc_mcp.app import (
        DEFAULT_GLOBAL_CONCURRENCY_LIMIT,
        RateLimitMiddleware,
        _int_env,
        thread_limiter_tokens,
    )

    assert DEFAULT_GLOBAL_CONCURRENCY_LIMIT == 128
    assert thread_limiter_tokens(8) == 40 and thread_limiter_tokens(256) == 256
    monkeypatch.delenv("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", raising=False)
    assert _int_env("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", 128) == 128
    monkeypatch.setenv("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", "256")
    assert _int_env("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", 128) == 256
    for invalid in ("0", "abc", "-4", "10001"):
        monkeypatch.setenv("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", invalid)
        try:
            _int_env("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", 128)
        except ValueError:
            continue
        raise AssertionError(invalid)

    monkeypatch.setenv("PWC_MCP_REQUEST_LIMIT", "7")
    monkeypatch.setenv("PWC_MCP_SEMANTIC_LIMIT", "3")
    monkeypatch.setenv("PWC_MCP_CONCURRENCY_LIMIT", "2")
    monkeypatch.setenv("PWC_MCP_GLOBAL_CONCURRENCY_LIMIT", "64")
    app = create_app(StubCatalog(), allowed_hosts=["testserver"])
    layer = app
    while not isinstance(layer, RateLimitMiddleware):
        layer = layer.app
    assert (
        layer.request_limit,
        layer.semantic_limit,
        layer.concurrency_limit,
        layer.global_concurrency_limit,
    ) == (7, 3, 2, 64)
    with TestClient(app) as client:
        assert client.get("/health").status_code in {200, 503}
