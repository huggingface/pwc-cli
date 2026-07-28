---
name: pwc-cli
description: "Papers With Code CLI (`pwc`) for searching and reading AI/ML papers, discovering recent and trending research, finding related work and paper lineage, browsing tasks, methods, conferences, and benchmark leaderboards through the public Papers With Code catalog. Use whenever the user asks to find papers, survey literature, compare research, inspect an arXiv paper, explore AI/ML taxonomy or conferences, discover benchmarks or state-of-the-art models, or mentions Papers With Code, `pwc`, or the `pwc-cli`. Prefer this skill for grounded AI/ML research even when the user does not explicitly ask for a CLI command."
---

Install:

```bash
pwc_repo=https://raw.githubusercontent.com/huggingface/pwc-cli/main
curl -LsSf "$pwc_repo/standalone_cli/install.py" | python3
```

The `pwc` CLI is anonymous and read-only. It queries the public
[Papers With Code](https://paperswithcode.co) catalog and requires no token.
Run `pwc --help` or a nested `--help` command when the live parser and this skill
disagree; the parser is authoritative.

Use compact output for reading and discovery. Add `--json` when programmatic
filtering, joining, or schema-dependent processing is useful.

`PAPER` accepts a modern or legacy arXiv ID, a numeric external-paper ID, or an
exact paper title. Quote titles containing spaces. Title matching is
case-insensitive but exact; ambiguous titles fail with their matching IDs rather
than silently selecting a paper.

## Commands

- `pwc search QUERY` — Search papers by title, topic, author, or arXiv ID.
  `[--limit 1-100 --page 1-100 --mode hybrid|keyword|semantic --json]`
- `pwc paper info PAPER` — Show metadata, including the abstract.
  `--include-resources` adds Markdown sections for repositories, project pages,
  and Hugging Face artifacts, with official links marked explicitly.
  `[--include-resources --json]`
- `pwc paper read PAPER` — Print stored paper Markdown. The resolved paper must
  have a modern arXiv record. `[--json]`
- `pwc paper list` — List and filter the paper catalog.
  `[--page 1-100 --page-size 1-100 --search TEXT
  --published-after YYYY-MM-DD --published-before YYYY-MM-DD
  --conference TEXT --all-versions
  --order-by trending|date_published|citation_count
  --order-dir asc|desc --time today|week|month|all_time
  --include-resources --json]`
- `pwc paper recent` — List recently published papers.
  `[--limit 1-100 --json]`
- `pwc paper trending` — List papers with recent repository activity.
  `[--limit 1-100 --max-age-days 1-365 --min-velocity FLOAT --json]`
- `pwc paper related PAPER` — Find embedding- and taxonomy-ranked related work.
  `[--limit 1-20 --json]`
- `pwc paper lineage list PAPER` — Render linked Markdown sections for a paper,
  its predecessors, and its successors. `[--json]`
- `pwc task list` — List research tasks, optionally filtering by a
  case-insensitive exact area name such as Vision, Audio, or General.
  Interactive output groups visible top-level tasks into Markdown area sections;
  `--group-by-area` forces that view when output is captured, while `--flat`
  forces the paginated table.
  `[--page 1-100 --page-size 1-100 --area NAME_OR_ID --level INTEGER
  --visible-only --group-by-area|--flat
  --order-by name|created_at|level|paper_count
  --order-dir asc|desc --json]`
- `pwc method list` — List research methods, optionally filtering by area or
  introduction year.
  `[--page 1-100 --page-size 1-500 --area NAME_OR_ID
  --introduced-year YEAR
  --order-by name|full_name|introduced_year|created_at|paper_count
  --order-dir asc|desc --json]`
- `pwc conference list` — List conferences with imported papers.
  `[--year YEAR --json]`
- `pwc benchmark list` — Find and rank benchmarks. When `--task` is supplied,
  the default order is task-specific trending activity; otherwise it is name.
  `[--page 1-100 --page-size 1-100 --search TEXT --task TASK
  --include-descendants --min-eval-count INTEGER --is-open true|false
  --order-by trending|name|full_name|created_at|paper_count
  --order-dir asc|desc --json]`
- `pwc benchmark --name NAME` — Show a benchmark's top models, scores, source
  papers, publication dates, and open/closed status. `NAME` must exactly match
  a benchmark name, full name, slug, or ID.
  `[--limit 1-100 --is-open true|false --json]`
- `pwc version` — Show the CLI and API contract versions.

## Research workflow

1. Start with `pwc search` for a topic or a known paper. Use the default hybrid
   mode first; use keyword mode for exact terminology and semantic mode for
   conceptual matches.
2. Inspect promising results with `pwc paper info`. Add `--include-resources`
   when linked repositories, project pages, or Hugging Face artifacts matter.
3. Read primary evidence with `pwc paper read`. Do not treat search snippets or
   titles as sufficient support for detailed claims.
4. Expand the literature with `pwc paper related` and use
   `pwc paper lineage list` when model or method ancestry matters.
5. Explore the catalog taxonomy with `pwc task list --group-by-area`,
   `pwc method list`, and `pwc conference list`; use `--area` or `--year` to
   narrow broad lists.
6. Use `pwc benchmark list --task TASK` to discover active benchmarks, then
   `pwc benchmark --name NAME` to inspect a specific leaderboard.
7. Synthesize only after gathering enough primary evidence. Preserve paper
   titles, identifiers, and URLs in the answer so claims remain traceable.

## Command selection

- Use `pwc search` for relevance-ranked discovery.
- Use `pwc paper list` for structured filters, date windows, conferences, and
  deterministic sorting.
- Use `pwc paper recent` for recency and `pwc paper trending` for current
  repository activity; these are different signals.
- Use area names directly with task and method lists, for example
  `--area Vision`, `--area Audio`, or `--area General`. Area matching is
  case-insensitive; numeric area IDs are also accepted.
- Use `pwc conference list --year YEAR` for a specific conference edition year.
- Use `--include-resources` for linked repositories, project pages, and Hugging
  Face artifacts. Add `--json` when their structured metadata also matters.
- Use `--all-versions` only when individual arXiv versions are relevant.
- Use `--is-open true` to restrict benchmark results to open models.
- Paginate when stderr reports more results. Do not silently treat the first
  page as the complete catalog.

## Output and limits

- Interactive list and search output uses aligned text columns. When stdout is
  piped or captured, the same rows use lossless TSV for agent and script
  processing. Paper info uses labeled metadata and Markdown sections; benchmark
  details use Markdown.
- `--json` wraps responses as
  `{"schema_version":"v1","data":...}` for stable agent consumption.
- Stable exit codes are `0` success, `2` invalid usage, `3` network/server
  failure, and `4` invalid API response.
- `PWC_API_URL` may select another compatible v1 endpoint. The default is
  `https://paperswithcode.co/api/v1`.

The standalone parser contains no authentication, mutation, ingestion,
publication, image, CRON, embedding, or infrastructure commands. Do not
substitute repository-maintenance commands into a public or sandbox workflow.
