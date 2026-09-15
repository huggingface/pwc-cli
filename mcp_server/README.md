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

The default port is `7860`. Set `PORT` or `PWC_API_URL` to override the HTTP
port or compatible PwC API respectively.

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
the caller controls keyword or semantic mode. `read_paper` returns complete
stored Markdown when possible and an opaque continuation cursor for oversized
documents.

The server fails closed when a compatible catalog reports
`X-PwC-Truncated: 1`; it never presents partial Markdown as a complete paper.
End-to-end continuation for papers beyond the catalog's own response cap
therefore requires a cursor-capable upstream read endpoint before the hosted
beta is released.

## Resources

- `pwc://papers/{paper}`
- `pwc://papers/{paper}/markdown`
- `pwc://tasks/{task}`
- `pwc://benchmarks/{benchmark}`

## Configuration

| Variable | Purpose |
| --- | --- |
| `PWC_API_URL` | Compatible catalog API; defaults to production PwC |
| `PWC_MCP_ALLOWED_HOSTS` | Comma-separated HTTP Host allowlist |
| `PWC_MCP_ALLOWED_ORIGINS` | Comma-separated browser Origin allowlist |
| `PORT` | HTTP port; defaults to `7860` |
| `LOG_LEVEL` | Content-free operational log level |

Native clients may omit `Origin`. Browser requests must match the configured
allowlist. The server does not log queries, paper references, request bodies,
raw IP addresses, or authorization headers.

The hosted defaults allow 60 total requests and 10 semantic searches per minute
per client IP, with at most four concurrent requests. Tool inputs cap list
results at 25, catalog calls time out after 25 seconds, and request/upstream
response bodies are bounded to 2 MiB.

## Test

```bash
uv run --project mcp_server pytest mcp_server/tests
```

Releases are deployed from immutable `pwc-mcp-*` tags to the
`huggingface/paperswithcode-mcp` Docker Space after CLI, MCP, and container
checks pass. Tag deployment remains locked until the repository variable
`PWC_MCP_UPSTREAM_CURSOR_READY` is explicitly set to `true` after production
catalog continuation is available.
