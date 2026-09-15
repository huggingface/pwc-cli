# pwc MCP server

Anonymous, read-only Model Context Protocol access to the public
[Papers With Code](https://paperswithcode.co) catalog.

The server uses MCP `2026-07-28` over Streamable HTTP and serves legacy
2025-era clients on the same `/mcp` endpoint. It returns versioned structured
output with compact text fallbacks.

## Run locally

```bash
uv run --project mcp_server pwc-mcp
```

The default listener is `127.0.0.1:7860`. Set `PWC_MCP_HOST`, `PORT`, or
`PWC_API_URL` to override the bind address, HTTP port, or compatible PwC API.

```bash
curl http://127.0.0.1:7860/health
```

## Tools

- `search_papers`
- `list_papers`
- `get_paper_info`
- `read_paper`
- `get_related_papers`
- `get_paper_lineage`
- `get_task`
- `get_method`
- `list_benchmarks`
- `get_benchmark`

All tools are annotated read-only and idempotent. Search is deterministic;
the caller controls keyword or semantic mode. `read_paper` fetches at most one
64 KiB catalog chunk per call and returns a signed, one-hour continuation cursor
when more Markdown remains. Continuations stay pinned to the resolved paper and
content version, so a changed paper fails with an explicit restart response.

## Resources

- `pwc://papers/{paper}`
- `pwc://papers/{paper}/markdown`
- `pwc://tasks/{task}`
- `pwc://benchmarks/{benchmark}`

## Configuration

| Variable | Purpose |
| --- | --- |
| `PWC_API_URL` | Compatible catalog API; defaults to production PwC |
| `PWC_MCP_HOST` | Listener address; defaults to loopback (`127.0.0.1`) |
| `PWC_MCP_ALLOWED_HOSTS` | Comma-separated HTTP Host allowlist |
| `PWC_MCP_ALLOWED_ORIGINS` | Comma-separated browser Origin allowlist |
| `PWC_MCP_CURSOR_KEY_CURRENT` | Required secret used to sign continuation cursors |
| `PWC_MCP_CURSOR_KEY_PREVIOUS` | Optional previous secret accepted during key rotation |
| `PORT` | HTTP port; defaults to `7860` |
| `LOG_LEVEL` | Content-free operational log level |

Native clients may omit `Origin`. Browser requests must match the configured
allowlist. The server does not log queries, paper references, request bodies,
raw IP addresses, or authorization headers.

The hosted defaults allow 60 total requests and 10 semantic searches per minute
per client IP, with at most four concurrent requests per IP and 32 globally.
Tool inputs cap list results at 25, catalog calls time out after 25 seconds, and
request, upstream, and serialized MCP response bodies are bounded to 2 MiB.
Markdown chunks use a bounded 256-entry/16 MiB in-memory cache.

## Test

```bash
uv run --project mcp_server pytest mcp_server/tests
```

The canonical hosted service is deployed as an immutable VPS component and
published through `https://paperswithcode.co/mcp`. The
`huggingface/paperswithcode-mcp` Docker Space remains an optional fallback; its
container explicitly binds to `0.0.0.0`.
