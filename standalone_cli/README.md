# pwc CLI

A lightweight, read-only client for the public Papers With Code catalog API.
Public reads are anonymous: no login or token is required.

```bash
uv tool install ./standalone_cli
pwc search "small vision language models" --limit 10
pwc paper info 2501.01234
pwc paper info 2501.01234 --include-resources
pwc paper info "Attention Is All You Need"
pwc paper read 2501.01234
pwc task list --area Vision
pwc task list --group-by-area
pwc method list --area Audio
pwc conference list --year 2025
pwc benchmark list
pwc skills add
```

Every `PAPER` argument accepts an arXiv ID, numeric external-paper ID, or quoted
exact title. Title matching is case-insensitive and rejects ambiguity.

Task lists and benchmark lists render grouped Markdown in a terminal. Use
`--flat` for paginated tables or `--group-by-area` to force Markdown when
piping. Task, search, pagination, ordering, and open-model filters take
precedence over `--group-by-area` and select task-scoped flat output. Grouped
benchmarks include their name, full name when distinct, slug, and ID.
Task-scoped benchmark lists also include the slug and ID alongside the best
model, paper, and code repository. Inspect one with
`pwc benchmark --name IDENTIFIER`; either identifier is accepted. Benchmark
details use an aligned table in a terminal and Markdown when piped or captured.
Other list and search output uses aligned columns in a terminal and lossless
TSV when captured.

Task lists report each task's direct paper, benchmark, and evaluation counts in
both grouped and flat output.

Leaderboard inspection supports numeric multi-metric selection:

```bash
pwc benchmark --name coco-val2017 --require-metrics mAP,FPS
pwc benchmark --name coco-val2017 --min FPS=60 --sort mAP:desc
pwc benchmark --name coco-val2017 --pareto mAP:higher,FPS:higher
```

`--min` and `--max` are repeatable. Metric names are case-insensitive. Metric
selection scans the bounded leaderboard before applying `--limit`; it fails
explicitly rather than returning an incomplete selection if a leaderboard
exceeds the 1,000-row scan ceiling.

`pipx install ./standalone_cli` is also supported. `PWC_API_URL` selects a
compatible versioned API origin; it defaults to
`https://paperswithcode.co/api/v1`. Run `pwc --help` for the complete read-only
command tree. The stable exit codes are 0 (success), 2 (usage), 3 (network or
server failure), and 4 (invalid response).

Install the version-matched Skill for coding agents with `pwc skills add`.
Add `--global` to use `~/.agents/skills`, `--claude` to also link it for Claude
Code, or `--dest PATH` for another harness.
