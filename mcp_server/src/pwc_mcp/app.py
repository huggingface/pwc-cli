from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from pwc_cli.transport import ResponseError, TransportError
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pwc_mcp import __version__
from pwc_mcp.catalog import CatalogClient
from pwc_mcp.server import Catalog, build_server

LOGGER = logging.getLogger("pwc_mcp.requests")
# MCP SDK diagnostics can include peer-supplied tool names and resource URIs.
# OperationalTelemetryMiddleware is the server's sole request log surface.
logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
PROTOCOL_VERSION = "2025-11-25"
EXPERIMENTAL_PROTOCOL_VERSION = "2026-07-28"
MAX_REQUEST_BODY_SIZE = 2 * 1024 * 1024
MAX_RESPONSE_BODY_SIZE = 2 * 1024 * 1024
KNOWN_TOOLS = {
    "search_papers",
    "list_papers",
    "get_paper_info",
    "read_paper",
    "get_related_papers",
    "get_trending_papers",
    "get_paper_evaluations",
    "get_paper_lineage",
    "get_task",
    "list_tasks",
    "get_method",
    "list_methods",
    "list_benchmarks",
    "get_benchmark",
}
KNOWN_PROTOCOLS = {
    PROTOCOL_VERSION,
    EXPERIMENTAL_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}


async def well_known_mcp(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "name": "Papers With Code",
            "description": "Anonymous read-only AI research catalog",
            "transport": {"type": "streamable-http", "url": "/mcp"},
            "protocol_version": PROTOCOL_VERSION,
            "supported_protocol_versions": sorted(KNOWN_PROTOCOLS, reverse=True),
            "documentation_url": "https://paperswithcode.co/mcp/schema",
            "setup_url": "https://paperswithcode.co/mcp",
        }
    )


def _docs_schema(server) -> dict:
    tools = []
    for tool in server._tool_manager.list_tools():
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
                "outputSchema": tool.output_schema,
            }
        )
    return {
        "name": "Papers With Code MCP",
        "version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "endpoint": "/mcp",
        "tools": tools,
    }


class MCPMethodMiddleware:
    """Avoid opening an SSE response for unsupported bare GET requests."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("path") == "/mcp"
            and scope.get("method") == "GET"
        ):
            response = JSONResponse(
                {"error": "method_not_allowed", "allowed": ["POST"]},
                status_code=405,
                headers={"Allow": "POST"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _csv_env(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


class CatalogReadiness:
    def __init__(self, catalog: CatalogClient | None, *, initially_ready: bool = False):
        self.catalog = catalog
        self.ready = initially_ready
        self.probing = False
        self.next_probe_at = 0.0
        self.lock = threading.Lock()

    def schedule(self) -> None:
        if self.catalog is None:
            return
        now = time.monotonic()
        with self.lock:
            if self.probing or now < self.next_probe_at:
                return
            self.probing = True
        asyncio.create_task(asyncio.to_thread(self._probe))

    def _probe(self) -> None:
        try:
            assert self.catalog is not None
            ready = self.catalog.check_readiness()
        except (ResponseError, TransportError):
            ready = False
        with self.lock:
            self.ready = ready
            self.probing = False
            self.next_probe_at = time.monotonic() + (30 if ready else 5)


async def health(request: Request) -> JSONResponse:
    readiness: CatalogReadiness = request.app.state.pwc_catalog_readiness
    readiness.schedule()
    ready = readiness.ready
    return JSONResponse(
        {
            "status": "ok" if ready else "unavailable",
            "service": "pwc-mcp",
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
        },
        status_code=200 if ready else 503,
    )


def _client_address(scope: Scope, headers: Headers, trust_proxy_headers: bool) -> str:
    client = scope.get("client")
    direct_address = str(client[0]) if client else "unknown"
    try:
        trusted_hop = ipaddress.ip_address(direct_address).is_loopback
    except ValueError:
        trusted_hop = False
    if trust_proxy_headers and trusted_hop:
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return direct_address


class RequestBodyTooLarge(Exception):
    pass


class ResponseSizeLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("path") != "/mcp"
            or scope.get("method") != "POST"
        ):
            await self.app(scope, receive, send)
            return
        messages: list[Message] = []
        size = 0
        oversized = False

        async def capture(message: Message) -> None:
            nonlocal size, oversized
            if message["type"] == "http.response.body":
                size += len(message.get("body", b""))
                oversized = oversized or size > MAX_RESPONSE_BODY_SIZE
            if not oversized:
                messages.append(message)

        await self.app(scope, receive, capture)
        if oversized:
            response = JSONResponse({"error": "response_too_large"}, status_code=503)
            await response(scope, receive, send)
            return
        for message in messages:
            await send(message)


async def _body_and_replay(
    receive: Receive, *, maximum_bytes: int
) -> tuple[bytes, Receive]:
    messages: list[Message] = []
    chunks = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > maximum_bytes:
            raise RequestBodyTooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    pending = deque(messages)

    async def replay() -> Message:
        if pending:
            return pending.popleft()
        return await receive()

    return b"".join(chunks), replay


def _tool_and_semantic(headers: Headers, body: bytes) -> tuple[str | None, bool]:
    header_tool = headers.get("mcp-name")
    body_tool = None
    mode = None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        tool = header_tool if header_tool in KNOWN_TOOLS else None
        return tool, False
    if isinstance(payload, dict):
        params = payload.get("params")
        if isinstance(params, dict):
            body_tool = (
                params.get("name") if isinstance(params.get("name"), str) else None
            )
    if isinstance(payload, dict):
        params = payload.get("params")
        arguments = params.get("arguments") if isinstance(params, dict) else None
        if isinstance(arguments, dict):
            mode = arguments.get("mode")
    tool = body_tool or header_tool
    tool = tool if tool in KNOWN_TOOLS else None
    return tool, tool == "search_papers" and mode == "semantic"


def _protocol_label(headers: Headers) -> str:
    value = headers.get("mcp-protocol-version")
    if value is None:
        return "legacy"
    return value if value in KNOWN_PROTOCOLS else "unknown"


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        request_limit: int,
        semantic_limit: int,
        concurrency_limit: int,
        global_concurrency_limit: int,
        trust_proxy_headers: bool,
        identity_limit: int = 10_000,
    ):
        self.app = app
        self.request_limit = request_limit
        self.semantic_limit = semantic_limit
        self.concurrency_limit = concurrency_limit
        self.global_concurrency_limit = global_concurrency_limit
        self.trust_proxy_headers = trust_proxy_headers
        self.identity_limit = identity_limit
        self.key = secrets.token_bytes(32)
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.semantic_requests: dict[str, deque[float]] = defaultdict(deque)
        self.active: dict[str, int] = defaultdict(int)
        self.lock = asyncio.Lock()
        self.next_cleanup_at = 0.0
        self.global_active = 0

    def _identity(self, address: str) -> str:
        return hashlib.blake2b(
            address.encode(), key=self.key, digest_size=16
        ).hexdigest()

    @staticmethod
    def _trim(values: deque[float], now: float) -> None:
        while values and values[0] <= now - 60:
            values.popleft()

    def _purge_stale_identities(self, now: float) -> None:
        for identity in list(self.requests):
            requests = self.requests[identity]
            semantic_requests = self.semantic_requests.get(identity)
            self._trim(requests, now)
            if semantic_requests is not None:
                self._trim(semantic_requests, now)
            if not requests and not semantic_requests and not self.active.get(identity):
                self.requests.pop(identity, None)
                self.semantic_requests.pop(identity, None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("path") != "/mcp"
            or scope.get("method") != "POST"
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        try:
            body, replay = await _body_and_replay(
                receive, maximum_bytes=MAX_REQUEST_BODY_SIZE
            )
        except RequestBodyTooLarge:
            response = JSONResponse({"error": "request_too_large"}, status_code=413)
            await response(scope, receive, send)
            return
        tool, semantic = _tool_and_semantic(headers, body)
        scope.setdefault("state", {})["pwc_tool_name"] = tool
        identity = self._identity(
            _client_address(scope, headers, self.trust_proxy_headers)
        )
        now = time.monotonic()
        limited = False
        saturated = False
        requests: deque[float] | None = None
        semantic_requests: deque[float] | None = None
        async with self.lock:
            saturated = self.global_active >= self.global_concurrency_limit
            if not saturated and (
                identity not in self.requests
                and len(self.requests) >= self.identity_limit
            ):
                if now >= self.next_cleanup_at:
                    self._purge_stale_identities(now)
                    self.next_cleanup_at = now + 5
                limited = len(self.requests) >= self.identity_limit
            if not limited and not saturated:
                requests = self.requests[identity]
                semantic_requests = self.semantic_requests[identity]
                self._trim(requests, now)
                self._trim(semantic_requests, now)
                limited = (
                    len(requests) >= self.request_limit
                    or (semantic and len(semantic_requests) >= self.semantic_limit)
                    or self.active[identity] >= self.concurrency_limit
                )
            if not limited and not saturated:
                assert requests is not None
                assert semantic_requests is not None
                requests.append(now)
                if semantic:
                    semantic_requests.append(now)
                self.active[identity] += 1
                self.global_active += 1
        if saturated:
            response = JSONResponse(
                {"error": "server_saturated"},
                status_code=503,
                headers={"Retry-After": "1"},
            )
            await response(scope, replay, send)
            return
        if limited:
            response = JSONResponse(
                {"error": "rate_limit_exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
            await response(scope, replay, send)
            return
        try:
            await self.app(scope, replay, send)
        finally:
            async with self.lock:
                self.active[identity] -= 1
                if self.active[identity] == 0:
                    self.active.pop(identity, None)
                self.global_active -= 1


class OperationalTelemetryMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/mcp":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500
        size = 0

        async def observe(message: Message) -> None:
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            headers = Headers(scope=scope)
            tool = scope.get("state", {}).get("pwc_tool_name")
            if tool not in KNOWN_TOOLS:
                tool = "unknown"
            LOGGER.info(
                "mcp_request tool=%s status=%s latency_ms=%s bytes=%s protocol=%s version=%s",
                tool or "unknown",
                status,
                round((time.monotonic() - started) * 1000),
                size,
                _protocol_label(headers),
                __version__,
            )


def create_app(
    catalog: Catalog | None = None,
    *,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    request_limit: int = 60,
    semantic_limit: int = 10,
    concurrency_limit: int = 4,
    global_concurrency_limit: int = 32,
    trust_proxy_headers: bool = True,
) -> ASGIApp:
    hosts = allowed_hosts or _csv_env(
        "PWC_MCP_ALLOWED_HOSTS",
        [
            "huggingface-paperswithcode-mcp.hf.space",
            "localhost:*",
            "127.0.0.1:*",
        ],
    )
    origins = allowed_origins or _csv_env(
        "PWC_MCP_ALLOWED_ORIGINS",
        ["https://chatgpt.com", "https://claude.ai"],
    )
    if "*" in origins:
        raise ValueError("PWC_MCP_ALLOWED_ORIGINS must not contain a wildcard")
    catalog_client = CatalogClient() if catalog is None else None
    resolved_catalog = catalog or catalog_client
    assert resolved_catalog is not None
    server = build_server(resolved_catalog)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=MAX_REQUEST_BODY_SIZE,
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        ),
    )
    app.routes.insert(0, Route("/health", health, methods=["GET"]))
    docs_schema = _docs_schema(server)

    async def docs(_request: Request) -> JSONResponse:
        return JSONResponse(docs_schema)

    app.routes.insert(1, Route("/docs", docs, methods=["GET"]))
    app.routes.insert(2, Route("/.well-known/mcp", well_known_mcp, methods=["GET"]))
    app.state.pwc_catalog_readiness = CatalogReadiness(
        catalog_client,
        initially_ready=catalog is not None,
    )
    wrapped: ASGIApp = RateLimitMiddleware(
        app,
        request_limit=request_limit,
        semantic_limit=semantic_limit,
        concurrency_limit=concurrency_limit,
        global_concurrency_limit=global_concurrency_limit,
        trust_proxy_headers=trust_proxy_headers,
    )
    wrapped = MCPMethodMiddleware(wrapped)
    wrapped = ResponseSizeLimitMiddleware(wrapped)
    wrapped = OperationalTelemetryMiddleware(wrapped)
    return CORSMiddleware(
        wrapped,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "mcp-protocol-version",
            "mcp-method",
            "mcp-name",
            "mcp-session-id",
        ],
        expose_headers=["mcp-session-id", "retry-after"],
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(
        create_app(),
        host=os.environ.get("PWC_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "7860")),
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
