# Read-only pwc CLI

Use compact text by default. Add `--json` only before a programmatic filter,
join, or schema-dependent transformation.

```bash
pwc search "QUERY" --limit 10
pwc paper info PAPER --include-resources
pwc paper read PAPER
pwc paper list --search "QUERY"
pwc paper recent
pwc paper trending
pwc paper related PAPER
pwc paper lineage list PAPER
pwc task list --group-by-area [--area AREA]
pwc method list --area AREA
pwc conference list --year YEAR
pwc benchmark list --task TASK
pwc benchmark --name "BENCHMARK"
```

`PAPER` may be an arXiv ID, numeric external-paper ID, or quoted exact title.
Titles are matched case-insensitively and ambiguous titles are rejected.

Run `pwc COMMAND --help` or `pwc paper COMMAND --help` when a flag is unclear.
The distributed client has no mutation, publication, authentication, network
fetch, diagnostics, image, embedding, or maintenance commands.
