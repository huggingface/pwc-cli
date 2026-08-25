"""Generate and install the pwc CLI skill for AI coding agents."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from pwc_cli import __version__

SKILL_NAME = "pwc-cli"
CENTRAL_LOCAL = Path(".agents/skills")
CENTRAL_GLOBAL = Path("~/.agents/skills")
CLAUDE_LOCAL = Path(".claude/skills")
CLAUDE_GLOBAL = Path("~/.claude/skills")

_DESCRIPTION = (
    "Papers With Code CLI (`pwc`) for searching and reading AI/ML papers, "
    "discovering recent and trending research, finding related work and paper "
    "lineage, browsing tasks, methods, conferences, organizations, frameworks, "
    "and benchmark leaderboards "
    "through the public Papers With Code catalog. Use whenever the user asks to "
    "find papers, survey literature, compare research, inspect an arXiv paper, "
    "explore AI/ML taxonomy or conferences, discover benchmarks or "
    "state-of-the-art models, or mentions Papers With Code, `pwc`, or `pwc-cli`."
)

_INTRODUCTION = """
The `pwc` CLI is anonymous and read-only. It queries the public
[Papers With Code](https://paperswithcode.co) catalog and requires no token.
Run `pwc --help` or a nested `--help` command when the live parser and this
skill disagree; the parser is authoritative.

Use compact output for reading and discovery. Add `--json` for programmatic
filtering, joining, or schema-dependent processing.

When the user identifies an author, prefer repeatable structured
`pwc paper list --author AUTHOR` filters. Add `--search TEXT` for stated topic
terms and explicit `--order-by date_published --order-dir desc` for newest or
recent work. Author references accept an exact normalized name, numeric ID, or
`@HF_USERNAME`; repeated authors use AND semantics.

Publication date ranges are inclusive: use `--start-date YYYY-MM-DD` and
`--end-date YYYY-MM-DD` with `pwc search` or `pwc paper list`; the start date
must not be later than `--end-date`.

Paper discovery commands accept `--implementation-coverage` to add official
implementation status and total linked repository count columns. JSON and
`pwc paper info` always include both fields. Use
`--has-official-implementation` with `pwc search` or `pwc paper list` to require
a catalog-linked official repository; these filters fail closed if unconfirmed.

Use `pwc benchmark --name NAME --max-parameters SIZE` to keep models at or
below an inclusive parameter limit. SIZE accepts values such as `500M`, `1.5B`,
`3B`, and raw integers. Models without one consistent parameter count are
excluded from constrained results.

`PAPER` accepts a modern or legacy arXiv ID, a numeric external-paper ID, or an
exact paper title. Quote titles containing spaces. Title matching is
case-insensitive but exact; ambiguous titles fail with their matching IDs.
"""

_WORKFLOW = """
## Research workflow

1. Use `pwc benchmark list --task TASK` to discover active benchmarks, then
   `pwc benchmark --name NAME` to inspect a leaderboard. Add
   `--max-parameters SIZE` when model size is part of the request.
2. Use `pwc paper info` to inspect promising results. Add
   `--include-resources` when repositories, project pages, or Hugging Face
   artifacts matter.
3. Use exact `pwc paper list --author`, `--task`, `--method`, `--conference`,
   `--framework`, and `--organization` filters for known identities or catalog
   associations. Combine them to require every association; do not substitute
   a keyword search. Add `--search` for title or abstract topic terms.
4. Use `pwc search` for broader discovery, then `pwc paper read` for primary
   evidence.
5. Expand the literature with `pwc paper related` and use
   `pwc paper lineage list` when model or method ancestry matters.
6. Preserve paper titles, identifiers, and URLs so claims remain traceable.

## Output and limits

- Interactive output is optimized for people. Benchmark lists remain aligned
  when captured; other captured list output uses lossless TSV. Add `--json` for
  structured agent or script consumption.
- Stable exit codes are `0` success, `2` invalid usage, `3` network/server
  failure, and `4` invalid API response.
- `PWC_API_URL` may select another compatible v1 endpoint. The default is
  `https://paperswithcode.co/api/v1`.
- Catalog-filtered paper lists fail closed unless the server confirms every
  requested filter; never treat results from an older server as filtered.
- Parameter-filtered benchmark details fail closed unless the server confirms
  parameter-filter support and every returned model satisfies the limit.

The research commands contain no authentication, catalog mutation, ingestion,
publication, image, embedding, CRON, or infrastructure-maintenance operations.
"""


class SkillInstallError(Exception):
    """An invalid or unsafe skill installation request."""


def _subcommand_help(action: argparse._SubParsersAction, name: str) -> str:
    for choice in action._choices_actions:
        if choice.dest == name:
            return str(choice.help or "")
    return ""


def _command_signature(parser: argparse.ArgumentParser) -> str:
    parts: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        if action.dest == "help":
            continue
        if not action.option_strings:
            parts.append(str(action.metavar or action.dest).upper())
            continue
        option = next(
            (item for item in action.option_strings if item.startswith("--")),
            action.option_strings[0],
        )
        if action.nargs == 0:
            parts.append(f"[{option}]")
            continue
        if action.choices:
            value = "|".join(str(choice) for choice in action.choices)
        else:
            value = str(action.metavar or action.dest).upper()
        parts.append(f"[{option} {value}]")
    return " ".join(parts)


def _command_entries(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()
) -> list[str]:
    entries: list[str] = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            path = (*prefix, name)
            if "handler" in child._defaults:
                signature = _command_signature(child)
                invocation = " ".join(("pwc", *path, signature)).rstrip()
                help_text = _subcommand_help(action, name)
                suffix = f" — {help_text.rstrip('.')}" if help_text else ""
                entries.append(f"- `{invocation}`{suffix}.")
            entries.extend(_command_entries(child, path))
    return entries


def build_skill_md() -> str:
    """Build a Skill matching the commands in the installed pwc version."""
    from pwc_cli.cli import build_parser

    commands = "\n".join(_command_entries(build_parser()))
    return (
        "---\n"
        f"name: {SKILL_NAME}\n"
        f'description: "{_DESCRIPTION}"\n'
        "---\n\n"
        f"Generated with `pwc v{__version__}`. Run "
        "`pwc skills add --force` to regenerate.\n"
        f"{_INTRODUCTION}\n"
        "## Commands\n\n"
        f"{commands}\n"
        f"{_WORKFLOW}"
    )


def _remove_existing(path: Path, force: bool) -> None:
    if not (path.exists() or path.is_symlink()):
        return
    if not force:
        raise SkillInstallError(
            f"skill already exists at {path}; rerun with --force to overwrite"
        )
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _install_to(skills_dir: Path, force: bool) -> Path:
    skills_dir = skills_dir.expanduser().resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)
    target = skills_dir / SKILL_NAME
    _remove_existing(target, force)
    temporary = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=skills_dir))
    try:
        (temporary / "SKILL.md").write_text(build_skill_md(), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def _link_to(skills_dir: Path, installed: Path, force: bool) -> Path:
    skills_dir = skills_dir.expanduser().resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)
    link = skills_dir / SKILL_NAME
    _remove_existing(link, force)
    link.symlink_to(os.path.relpath(installed, skills_dir), target_is_directory=True)
    return link


def skills_add(args: argparse.Namespace, _client: object) -> int:
    """Install the generated pwc skill in central and optional harness paths."""
    if args.dest is not None and (args.global_ or args.claude):
        raise SkillInstallError("--dest cannot be combined with --global or --claude")

    if args.dest is not None:
        installed = _install_to(args.dest, args.force)
        print(f"Installed '{SKILL_NAME}' to {installed}")
        return 0

    central_root = CENTRAL_GLOBAL if args.global_ else CENTRAL_LOCAL
    installed = _install_to(central_root, args.force)
    print(f"Installed '{SKILL_NAME}' to {installed}")

    if args.claude:
        claude_root = CLAUDE_GLOBAL if args.global_ else CLAUDE_LOCAL
        link = _link_to(claude_root, installed, args.force)
        print(f"Linked '{SKILL_NAME}' for Claude at {link}")
    return 0
