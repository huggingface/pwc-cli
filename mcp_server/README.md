# pwc MCP server

Anonymous, read-only Model Context Protocol access to the public
[Papers With Code](https://paperswithcode.co) catalog.

The server uses stock-client MCP `2025-11-25` over Streamable HTTP and also
serves the experimental `2026-07-28` discovery protocol on the same `/mcp`
endpoint. It returns versioned structured output with compact Markdown
fallbacks.

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
- `get_trending_papers`
- `get_paper_evaluations`
- `get_paper_lineage`
- `get_task`
- `list_tasks`
- `get_method`
- `list_methods`
- `list_benchmarks`
- `get_benchmark`

All tools are annotated read-only and idempotent. Expected failures use typed
messages (`not_found`, `ambiguous`, `no_markdown`, and `upstream_timeout`), with
candidate IDs and slugs for ambiguous references. Search is deterministic;
the caller controls keyword or semantic mode. `read_paper` fetches at most one
64 KiB catalog chunk per call and returns a signed, one-hour continuation cursor
when more Markdown remains. Continuations stay pinned to the resolved paper and
content version, so a changed paper fails with an explicit restart response.
Benchmark and paper evaluations are paginated. Equivalent duplicate rows are
merged while preserving every task-specific rank scope; rows also expose the
reported protocol, split, shot count when stated, source, update timestamp,
openness, and metric direction.

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

`GET /.well-known/mcp` exposes connection metadata and `GET /docs` publishes
the live input/output schema for every tool. On the canonical host these are
also available as `/.well-known/mcp` and `/mcp/schema`, while a human GET of
`/mcp` opens the setup guide. Direct package servers return `405` for a bare
`GET /mcp`; MCP requests use `POST /mcp`. Rate limits return `429` with
`Retry-After`.

## Test

```bash
uv run --project mcp_server pytest mcp_server/tests
```

The canonical hosted service is deployed as an immutable VPS component and
published through `https://paperswithcode.co/mcp`. The
`huggingface/paperswithcode-mcp` Docker Space remains an optional fallback; its
container explicitly binds to `0.0.0.0`.
