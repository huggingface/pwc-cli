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
pwc method list --area Audio
pwc conference list --year 2025
```

Every `PAPER` argument accepts an arXiv ID, numeric external-paper ID, or quoted
exact title. Title matching is case-insensitive and rejects ambiguity.

List and search output uses aligned columns in a terminal and lossless TSV when
piped or captured.

`pipx install ./standalone_cli` is also supported. `PWC_API_URL` selects a
compatible versioned API origin; it defaults to
`https://paperswithcode.co/api/v1`. Run `pwc --help` for the complete read-only
command tree. The stable exit codes are 0 (success), 2 (usage), 3 (network or
server failure), and 4 (invalid response).
