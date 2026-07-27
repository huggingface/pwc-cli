# pwc CLI

A fast, read-only command-line client for exploring the public
[Papers with Code](https://paperswithcode.co) catalog.

Use `pwc` to search papers, read paper metadata and Markdown, discover recent,
trending, and related work, inspect paper lineage, and browse benchmarks. Public
reads are anonymous: no account, API token, or Python environment is required.

```bash
pwc search "small vision language models" --limit 3
pwc paper info 2501.01234
```

## Installation

### Prebuilt binary (recommended)

The installer downloads the binary for your platform, verifies its SHA-256
checksum, and installs it as `~/.local/bin/pwc`:

```bash
pwc_repo=https://raw.githubusercontent.com/huggingface/pwc-cli/main
curl -LsSf "$pwc_repo/standalone_cli/install.py" | python3
```

The installer selects the latest release by default. Pass `--version VERSION`
after `python3 -` to explicitly pin a release. It also reports whether
`~/.local/bin` is on `PATH` and prints a persistent shell instruction when
needed.


Prebuilt releases support:

- Linux x86-64
- macOS Intel (x86-64)
- macOS Apple Silicon (arm64)

Every release includes checksum files and GitHub build-provenance attestations.
See the [latest release](https://github.com/huggingface/pwc-cli/releases/latest)
to download and verify an artifact manually.

### Install from source

Python 3.10 or newer is required for source installations.

With [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install 'git+https://github.com/huggingface/pwc-cli.git#subdirectory=standalone_cli'
```

Or with [`pipx`](https://pipx.pypa.io/):

```bash
pipx install 'git+https://github.com/huggingface/pwc-cli.git#subdirectory=standalone_cli'
```

To work from a clone instead:

```bash
git clone https://github.com/huggingface/pwc-cli.git
cd pwc-cli
uv tool install ./standalone_cli
```

Confirm the installation:

```bash
pwc version
```

## Quick start

Search the paper catalog:

```bash
pwc search "retrieval augmented generation"
pwc search "vision transformers" --mode semantic --limit 20
pwc search "attention" --page 2 --limit 10
```

Inspect or read a paper:

```bash
pwc paper info 1706.03762
pwc paper info 1706.03762 --include-resources
pwc paper read 1706.03762
```

Discover papers:

```bash
pwc paper recent --limit 10
pwc paper trending --limit 20 --max-age-days 90
pwc paper related 1706.03762 --limit 4
pwc paper lineage list 1706.03762
```

List and filter papers:

```bash
pwc paper list --conference NeurIPS --page-size 20
pwc paper list --search "diffusion" --published-after 2025-01-01
pwc paper list --order-by citation_count --order-dir desc
```

Browse benchmarks:

```bash
pwc benchmark list --search ImageNet
pwc benchmark list --task OCR
pwc benchmark list --task image-classification --include-descendants
pwc benchmark list --min-eval-count 10 --order-by paper_count --order-dir desc
pwc benchmark --name "SWE-Bench Pro"
```

## Command reference

| Command | Description |
| --- | --- |
| `pwc search QUERY` | Search for papers by title, topic, author, or arXiv ID |
| `pwc paper info PAPER` | Show concise paper metadata |
| `pwc paper read PAPER` | Print the stored paper Markdown |
| `pwc paper list` | List and filter papers |
| `pwc paper recent` | List recently published papers |
| `pwc paper trending` | List trending papers |
| `pwc paper related PAPER` | Find related papers |
| `pwc paper lineage list PAPER` | List a paper's predecessors and successors |
| `pwc benchmark list` | List and filter benchmarks, ranked by task trends when `--task` is used |
| `pwc benchmark --name NAME` | Show a benchmark's top models, papers, scores, and publication dates |
| `pwc version` | Show the CLI and API contract versions |

Run `pwc --help`, `pwc COMMAND --help`, or
`pwc paper COMMAND --help` for all available options.

## Output and scripting

Commands emit compact, deterministic TSV or labeled text by default. Add
`--json` to any data command for machine-readable output:

```bash
pwc search "language models" --limit 5 --json
pwc paper info 1706.03762 --json
pwc benchmark list --task image-classification --json
```

JSON responses include a top-level `schema_version` and `data` field:

```json
{
  "schema_version": "v1",
  "data": {}
}
```

The stable exit codes are:

| Code | Meaning |
| ---: | --- |
| `0` | Success |
| `2` | Invalid command or arguments |
| `3` | Network or server failure |
| `4` | Invalid API response |

## Configuration

`pwc` uses `https://paperswithcode.co/api/v1` by default. To connect to another
compatible v1 API, set `PWC_API_URL`:

```bash
PWC_API_URL=http://localhost:8000/api/v1 pwc search "transformers"
```

## Scope

The public CLI is intentionally anonymous and read-only. It does not include
authentication, mutation, ingestion, publication, image, embedding, CRON, or
infrastructure-maintenance commands.

## Development

Run the CLI from a checkout:

```bash
uv run --project standalone_cli pwc --help
```

Run the focused test and contract checks:

```bash
uv run --project standalone_cli --with pytest pytest standalone_cli/tests
uv run python standalone_cli/scripts/check_contract.py
```

The implementation lives in [`standalone_cli`](standalone_cli/).
