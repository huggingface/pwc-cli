# Papers With Code MCP server specification

This document records the agreed beta scope for the hosted Papers With Code MCP
server.

## Product and hosting

- Live in this repository as the separate `pwc-mcp` Python package and reuse the
  CLI's public HTTP transport rather than invoking the CLI as a subprocess.
- Ship the canonical endpoint from the Papers With Code VPS at
  `https://paperswithcode.co/mcp`. The public Docker Space
  `huggingface/paperswithcode-mcp` remains an optional fallback without changing
  the MCP contract.
- Serve anonymous, read-only requests. Search is deterministic and contains no
  embedded language model.
- Use stateless Streamable HTTP at `/mcp`, advertising stock-client MCP
  `2025-11-25` while also supporting experimental `2026-07-28` discovery.
  Expose `/health`, `/.well-known/mcp`, and a generated `/docs` schema.

## Public contract

Expose these tools:

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

Expose these resource templates:

- `pwc://papers/{paper}`
- `pwc://papers/{paper}/markdown`
- `pwc://tasks/{task}`
- `pwc://benchmarks/{benchmark}`

Expose the `find_papers`, `compare_leaderboard`, and `survey_task` prompts.

Responses use stable, MCP-specific versioned structured outputs with a text
fallback. `read_paper` performs one upstream read of at most 64 KiB per call and
returns a signed opaque continuation cursor when more Markdown remains. The
cursor binds the original reference, canonical paper, content version, byte
offset, chunk limit, key identifier, and fixed one-hour expiry. The current and
previous signing keys support rotation without accepting unsigned state.

Paper references accept arXiv IDs, numeric PwC external IDs, arXiv/Hugging
Face/Papers With Code URLs, and exact titles. Ambiguous exact titles fail rather
than selecting one result.

Benchmark and paper evaluation results paginate with `page` and `next_page`.
Equivalent result rows merge their metrics while retaining task-scoped ranks,
evaluation protocol, split, shots when reported, source URL, openness, and
update timestamp. Metric direction is explicit and unknown directions remain
`unknown` rather than being guessed.

## Safety and operations

- Require a strict configurable browser Origin allowlist; native clients may
  omit Origin. Never configure a wildcard Origin.
- Enforce, per IP, 60 total requests/minute, 10 semantic searches/minute, and
  four concurrent requests, plus a global ceiling of 32 concurrent requests.
  List tools return at most 25 rows.
- Set catalog timeouts to 25 seconds and bound HTTP request and upstream response
  bodies and serialized MCP responses to 2 MiB.
- Log only tool name, status, latency, response bytes, protocol, and server
  version. Never log queries, paper identifiers, raw IPs, authorization values,
  or request/response bodies.
- Cache tool/resource catalogs for one hour, taxonomy data for ten minutes,
  paper and benchmark data for five minutes, and search results for one minute.
  Cache immutable, versioned Markdown chunks for one hour within both a
  256-entry and 16 MiB ceiling.
- Bind to loopback on the VPS, trust forwarded identity only from an exact
  loopback peer, and expose cached catalog readiness without making `/health`
  wait on an upstream call.

## Release

Immutable releases must test the CLI contract and MCP server and build the
Docker image. VPS deployment smoke-tests `/health`, the single `/mcp` endpoint,
and the catalog continuation contract before nginx publication. Only tags
matching `pwc-mcp-*` may additionally publish the fallback Space.
