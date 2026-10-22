# pwc MCP server

Anonymous, read-only Model Context Protocol access to the public
[Papers With Code](https://paperswithcode.co) catalog.

The server uses stock-client MCP `2025-11-25` over Streamable HTTP and also
serves experimental `2026-07-28` discovery on the same `/mcp` endpoint. It
returns versioned structured output with compact Markdown fallbacks.

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

Every read-only `pwc` CLI research command is one tool, and every research flag
of that command is a tool parameter, so results match `pwc ... --json`. Each
result carries `schema_version` and `data`, the complete CLI JSON payload, next
to typed projections such as `items` or `evaluations`.

| Tool | CLI command |
| --- | --- |
| `search_papers` | `pwc search` |
| `get_paper_info` | `pwc paper info` |
| `get_paper_evaluations` | `pwc paper evaluations` |
| `read_paper` | `pwc paper read` (64 KiB chunks with a continuation cursor; MCP-only `offset` starts at a byte already held) |
| `list_papers` | `pwc paper list` |
| `list_recent_papers` | `pwc paper recent` |
| `list_trending_papers` | `pwc paper trending` |
| `get_related_papers` | `pwc paper related` |
| `get_paper_lineage` | `pwc paper lineage list` |
| `get_task` | `pwc task --name` |
| `list_tasks` | `pwc task list` |
| `get_method` | `pwc method --name` |
| `list_methods` | `pwc method list` |
| `get_conference` | `pwc conference --name` |
| `list_conferences` | `pwc conference list` |
| `get_organization` | `pwc organization --name` |
| `list_organizations` | `pwc organization list` |
| `get_framework` | `pwc framework --name` |
| `list_frameworks` | `pwc framework list` |
| `get_benchmark` | `pwc benchmark --name` |
| `list_benchmarks` | `pwc benchmark list` |

Parameter names follow the CLI flags except for the established MCP names
`published_after`/`published_before` (`--start-date`/`--end-date`), `limit`
(`--page-size`), `authors` (`--author`), `order_direction` (`--order-dir`),
`minimum_evaluations` (`--min-eval-count`), and `include_evaluations`
(`--include-evals`). Terminal-only flags (`--json`,
`--implementation-coverage`, `--flat`) have no parameter because MCP output is
always structured. Hosted differences from the CLI: `limit` is capped at 25,
`get_paper_info` returns compact official-first resources, and `read_paper`
is chunked. `search_papers` defaults to `hybrid` mode like `pwc search`.
`tests/test_parity.py` fails when the CLI parser and the tool schemas drift.

All tools are annotated read-only and idempotent. Search is deterministic;
the caller controls hybrid (default), keyword, or semantic mode. `read_paper`
fetches at most one 64 KiB catalog chunk per call and returns a signed, one-hour
continuation cursor when more Markdown remains. Continuations stay pinned to
the resolved paper and content version, so a changed paper fails with an
explicit restart response; any reference that resolves to the same paper (the
numeric catalog ID after starting from the arXiv ID, say) may carry the cursor.
A placeholder cursor such as `"next"` with nothing saved reads from the
beginning.

Paper tools fall back to the public API's `public-metadata` endpoint for papers
the catalog does not hold: `get_paper_info` and `read_paper` return Hugging Face
Papers, arXiv, or Crossref (DOI) metadata and the abstract with
`in_catalog: false`, a `note`, and labelled `available_at` links to cite instead
of a Papers With Code page; `get_related_papers` returns a catalog search for the
public title with the same `note` and links. A reference the endpoint maps to a
catalog paper returns the catalog paper. Titles go to the sibling
`public-metadata/search` endpoint, which accepts only an exact arXiv or Crossref
title match, or the single arXiv paper sharing the "Name:" before its colon.
`get_benchmark` answers a metric the leaderboard does not report with the
unfiltered leaderboard and a `metric_note` naming the available metrics.

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
| `PWC_MCP_REQUEST_LIMIT` | Requests per client per minute; defaults to `60` |
| `PWC_MCP_SEMANTIC_LIMIT` | Semantic or hybrid searches per client per minute; defaults to `10` |
| `PWC_MCP_CONCURRENCY_LIMIT` | Concurrent requests per client; defaults to `4` |
| `PWC_MCP_GLOBAL_CONCURRENCY_LIMIT` | Concurrent requests across all clients; defaults to `128` and sizes the tool thread pool |
| `PWC_MCP_TRUSTED_PROXY_CIDRS` | Comma-separated networks of a non-loopback reverse proxy whose `X-Forwarded-For` is trusted; empty by default |
| `LOG_LEVEL` | Content-free operational log level |

Native clients may omit `Origin`. Browser requests must match the configured
allowlist. A first-party client on the same host may send
`X-PwC-MCP-Client: <token>` to name its rate-limit identity (for example one
hashed chat session); the header counts only on a direct loopback connection
without `X-Forwarded-For`, so proxied public traffic cannot use it. The server does not log queries, paper references, request bodies,
raw IP addresses, or authorization headers.

Behind a reverse proxy that does not run on the same host, such as the Hugging
Face Spaces ingress, set `PWC_MCP_TRUSTED_PROXY_CIDRS` to the proxy's networks.
A request whose direct peer falls in one of them is limited by the right-most
`X-Forwarded-For` hop outside those networks; hops further left are
client-supplied and ignored. Such requests can never name an identity with
`X-PwC-MCP-Client`. Loopback peers keep the behaviour above. List only
networks that hold nothing but the proxy: any peer inside them can choose its
own rate-limit identity.

The hosted defaults allow 60 total requests and 10 semantic or hybrid searches per minute
per client IP, with at most four concurrent requests per IP and 128 globally.
Tool inputs cap list results at 25, catalog calls time out after 25 seconds, and
request, upstream, and serialized MCP response bodies are bounded to 2 MiB.
The global ceiling is 128 concurrent requests by default.
Markdown chunks use a bounded 256-entry/16 MiB in-memory cache.

`GET /.well-known/mcp` exposes connection metadata and `GET /docs` publishes
the live tool schema. A bare `GET /mcp` returns `405`; protocol requests use
`POST /mcp`. The server also exposes `find_papers`, `compare_leaderboard`, and
`survey_task` prompts.

## Test

```bash
uv run --project mcp_server pytest mcp_server/tests
```

The canonical hosted service is deployed as an immutable VPS component and
published through `https://paperswithcode.co/mcp`. The
`huggingface/paperswithcode-mcp` Docker Space remains an optional fallback; its
container explicitly binds to `0.0.0.0`.
