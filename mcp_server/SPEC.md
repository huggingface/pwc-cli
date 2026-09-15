# Papers With Code MCP server specification

This document records the agreed beta scope for the hosted Papers With Code MCP
server.

## Product and hosting

- Live in this repository as the separate `pwc-mcp` Python package and reuse the
  CLI's public HTTP transport rather than invoking the CLI as a subprocess.
- Ship first as the public Docker Space `huggingface/paperswithcode-mcp` on
  `cpu-upgrade` hardware. A VPS endpoint may replace it later without changing
  the MCP contract.
- Serve anonymous, read-only requests. Search is deterministic and contains no
  embedded language model.
- Use stateless Streamable HTTP at `/mcp`, supporting MCP `2026-07-28` and
  legacy 2025 clients on the same endpoint. Expose `/health` for operations.

## Public contract

Expose exactly these tools:

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

Expose these resource templates and no prompts:

- `pwc://papers/{paper}`
- `pwc://papers/{paper}/markdown`
- `pwc://tasks/{task}`
- `pwc://benchmarks/{benchmark}`

Responses use stable, MCP-specific versioned structured outputs with a text
fallback. `read_paper` returns complete stored Markdown when it fits and an
opaque continuation cursor only when it is oversized.

Paper references accept arXiv IDs, numeric PwC external IDs, arXiv/Hugging
Face/Papers With Code URLs, and exact titles. Ambiguous exact titles fail rather
than selecting one result.

## Safety and operations

- Require a strict configurable browser Origin allowlist; native clients may
  omit Origin. Never configure a wildcard Origin.
- Enforce, per IP, 60 total requests/minute, 10 semantic searches/minute, and
  four concurrent requests. List tools return at most 25 rows.
- Set catalog timeouts to 25 seconds and bound HTTP request and upstream response
  bodies to 2 MiB.
- Log only tool name, status, latency, response bytes, protocol, and server
  version. Never log queries, paper identifiers, raw IPs, authorization values,
  or request/response bodies.
- Cache tool/resource catalogs for one hour, taxonomy data for ten minutes,
  paper and benchmark data for five minutes, search results for one minute, and
  paper Markdown for one hour.

The hosted release requires a catalog read endpoint that can continue beyond
its own response cap. Until that upstream capability is available, the server
must fail closed when `X-PwC-Truncated: 1` rather than label partial Markdown as
complete.

## Release

Only tags matching `pwc-mcp-*` may publish the Space. Before upload, CI tests the
CLI contract and MCP server and builds the Docker image. The deployment uploads
the exact tagged source and smoke-tests `/health` after the Space becomes ready.
CI must reject release tags unless `PWC_MCP_UPSTREAM_CURSOR_READY=true` confirms
that the hosted-release Markdown prerequisite is available in production.
