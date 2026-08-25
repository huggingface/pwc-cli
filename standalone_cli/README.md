# pwc CLI

A lightweight, read-only client for the public Papers With Code catalog API.
Public reads are anonymous: no login or token is required.

```bash
uv tool install ./standalone_cli
pwc search "small vision language models" --limit 10
pwc paper info 2501.01234
pwc paper info 2501.01234 --include-resources
pwc paper info 2501.01234 --include-evals
pwc paper info "Attention Is All You Need"
pwc paper read 2501.01234
pwc task --name "scene-text-recognition"
pwc task list --area Vision
pwc task list --group-by-area
pwc method --name Transformer
pwc method list --area Audio
pwc conference --name "CVPR 2025"
pwc conference list --year 2025
pwc organization --name NVIDIA
pwc organization list --featured-only
pwc framework --name vLLM
pwc framework list --platform gpu
pwc benchmark list
pwc skills add
```

Every `PAPER` argument accepts an arXiv ID, numeric external-paper ID, or quoted
exact title. Title matching is case-insensitive and rejects ambiguity.

`pwc paper info PAPER --include-evals` fetches every evaluation associated with
the resolved paper. Human-readable output adds an Evaluations Markdown table;
`--json` adds an `evaluations` object containing `count` and `results`.
Normal paper-info output includes the names of tagged organizations when present.

Task lists and benchmark lists render grouped Markdown in a terminal. Use
`--flat` for paginated tables or `--group-by-area` to force Markdown when
piping. Task, search, pagination, ordering, and open-model filters take
precedence over `--group-by-area` and select task-scoped flat output. Grouped
benchmarks include their name, full name when distinct, slug, and ID.
Task-scoped benchmark lists also include the slug and ID alongside the best
model, paper, and code repository, using aligned columns even when output is
captured by an agent or another process. Inspect one with
`pwc benchmark --name IDENTIFIER`; either identifier is accepted. Benchmark
details use an aligned table in a terminal and Markdown when piped or captured.
Other list and search output uses aligned columns in a terminal and lossless
TSV when captured.

Paper discovery commands accept `--implementation-coverage` to add
`official_implementation` and `code_repositories` columns. JSON and
`pwc paper info` always include those fields. The boolean reports whether the
catalog links an official repository to any version of the paper; `false` does
not prove that no official code exists elsewhere. `pwc search` and
`pwc paper list` accept `--has-official-implementation` and fail closed unless
the API confirms the filter.

Task lists report each task's direct paper, benchmark, and evaluation counts in
both grouped and flat output.

`pwc task --name NAME` accepts an exact task name, slug, or ID and renders the
task-page details in CLI form: area, description, hierarchy, research trends,
recommended frameworks, sister tasks, subtasks, common methods, benchmarks,
and trending papers. Direct benchmarks follow the ranking returned by the
task benchmark-trends API; the CLI does not calculate or re-sort trend scores.
Add `--json` for the complete structured payload.

Methods, conferences, organizations, and frameworks share the same detail
shape as tasks: pass an exact name, slug, or ID to `--name`. Their `list`
subcommands retain entity-specific filters such as method area, conference
year, featured organizations, and framework domain, category, or platform.
`pwc paper list` accepts exact `--task`, `--method`, `--conference`,
`--framework`, and `--organization` filters. Repeat `--author` with exact
normalized names, numeric IDs, or `@HF_USERNAME` references to require every
listed co-author. Combine these filters to require every selected identity,
tag, or catalog association. The CLI fails closed if the selected API does not
confirm that it applied every requested catalog filter.
Publication date ranges use inclusive `--start-date YYYY-MM-DD` and
`--end-date YYYY-MM-DD` bounds; the start date cannot be later than the end date.

For example, find an author's papers on a stated topic in newest-first order:

```bash
pwc paper list --author "Kaiming He" --search drift \
  --order-by date_published --order-dir desc
```

Leaderboard inspection supports numeric multi-metric selection:

```bash
pwc benchmark --name coco-val2017 --require-metrics mAP,FPS
pwc benchmark --name coco-val2017 --min FPS=60 --sort mAP:desc
pwc benchmark --name coco-val2017 --pareto mAP:higher,FPS:higher
pwc benchmark --name wikitext-103 --is-open true --max-parameters 3B
```

`--min` and `--max` are repeatable. Metric names are case-insensitive. Metric
selection scans the bounded leaderboard before applying `--limit`; it fails
explicitly rather than returning an incomplete selection if a leaderboard
exceeds the 1,000-row scan ceiling.

`--max-parameters SIZE` keeps models at or below an inclusive parameter limit.
SIZE accepts case-insensitive decimal suffixes such as `500M`, `1.5B`, and
`3B`, plus raw integers. Parameter-constrained results exclude models whose
parameter count is unknown or inconsistent across metric rows and fail closed
against APIs that cannot confirm support. Human-readable leaderboard output
includes a Parameters column; JSON retains the exact `num_parameters` integer.

`pipx install ./standalone_cli` is also supported. `PWC_API_URL` selects a
compatible versioned API origin; it defaults to
`https://paperswithcode.co/api/v1`. Run `pwc --help` for the complete read-only
command tree. The stable exit codes are 0 (success), 2 (usage), 3 (network or
server failure), and 4 (invalid response).

Install the version-matched Skill for coding agents with `pwc skills add`.
Add `--global` to use `~/.agents/skills`, `--claude` to also link it for Claude
Code, or `--dest PATH` for another harness.
