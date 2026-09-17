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
  `2025-11-25` while supporting experimental `2026-07-28` discovery. Expose
  `/health`, `/.well-known/mcp`, and generated `/docs` schema routes.

## Public contract

Expose exactly one tool per read-only `pwc` CLI research command, with every
research flag of that command as a tool parameter (`tests/test_parity.py`
enforces this against the CLI parser):

- `search_papers` (`pwc search`)
- `get_paper_info` (`pwc paper info`)
- `get_paper_evaluations` (`pwc paper evaluations`)
- `read_paper` (`pwc paper read`)
- `list_papers` (`pwc paper list`)
- `list_recent_papers` (`pwc paper recent`)
- `list_trending_papers` (`pwc paper trending`)
- `get_related_papers` (`pwc paper related`)
- `get_paper_lineage` (`pwc paper lineage list`)
- `get_task` (`pwc task --name`)
- `list_tasks` (`pwc task list`)
- `get_method` (`pwc method --name`)
- `list_methods` (`pwc method list`)
- `get_conference` (`pwc conference --name`)
- `list_conferences` (`pwc conference list`)
- `get_organization` (`pwc organization --name`)
- `list_organizations` (`pwc organization list`)
- `get_framework` (`pwc framework --name`)
- `list_frameworks` (`pwc framework list`)
- `get_benchmark` (`pwc benchmark --name`)
- `list_benchmarks` (`pwc benchmark list`)

Tools run the CLI handlers in-process through the shared cached transport, so
validation, fail-closed filter confirmation, and the JSON payload are the
CLI's. Every result includes that payload as `data` beside typed projections.
Terminal-only flags have no parameter. The hosted service caps `limit` at 25,
defaults `search_papers` to keyword mode, returns compact official-first paper
resources, and serves `read_paper` in chunks.

Expose these resource templates:

- `pwc://papers/{paper}`
- `pwc://papers/{paper}/markdown`
- `pwc://tasks/{task}`
- `pwc://benchmarks/{benchmark}`

Expose `find_papers`, `compare_leaderboard`, and `survey_task` prompts.

Responses use stable, MCP-specific versioned structured outputs with a text
fallback. `read_paper` performs one upstream read of at most 64 KiB per call and
returns a signed opaque continuation cursor when more Markdown remains. The
cursor binds the original reference, canonical paper, content version, byte
offset, chunk limit, key identifier, and fixed one-hour expiry. The current and
previous signing keys support rotation without accepting unsigned state.

Paper references accept arXiv IDs, numeric PwC external IDs, arXiv/Hugging
Face/Papers With Code URLs, and exact titles. Ambiguous exact titles fail rather
than selecting one result.

Paper evaluations paginate. Leaderboards merge equivalent model rows across
task scopes while retaining scoped ranks, protocol, split, shots, source,
openness, and update time. Metric direction is explicit when known.

## Safety and operations

- Require a strict configurable browser Origin allowlist; native clients may
  omit Origin. Never configure a wildcard Origin.
- Enforce, per IP, 60 total requests/minute, 10 semantic or hybrid searches/minute, and
  four concurrent requests, plus a global ceiling of 128 concurrent requests.
  All four limits are overridable through `PWC_MCP_*_LIMIT` environment
  variables; the global ceiling also sizes the synchronous tool thread pool.
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
  loopback peer, let a direct loopback client without `X-Forwarded-For` name
  its own rate-limit identity with `X-PwC-MCP-Client`, and expose cached catalog readiness without making `/health`
  wait on an upstream call.

## Release

Immutable releases must test the CLI contract and MCP server and build the
Docker image. VPS deployment smoke-tests `/health`, the single `/mcp` endpoint,
and the catalog continuation contract before nginx publication. Only tags
matching `pwc-mcp-*` may additionally publish the fallback Space.
