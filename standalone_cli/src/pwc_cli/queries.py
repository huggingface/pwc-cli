"""In-process access to the CLI's read-only research queries.

Embedders such as the Papers With Code MCP server run the exact CLI handlers
through the CLI's own parser. Every flag, default, validation rule, and JSON
payload therefore stays identical to ``pwc ... --json`` without a subprocess,
stdout capture, or a second implementation of the query logic.

Each call owns its parser and result sink, so concurrent callers never share
state. Only read-only research commands are reachable; authentication, paper
editing, skill installation, and version display are refused.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import date
from typing import Any

from pwc_cli.cli import Parser, UsageError, build_parser
from pwc_cli.transport import ResponseError

Command = tuple[str, ...]

READ_ONLY_COMMANDS: tuple[Command, ...] = (
    ("search",),
    ("paper", "info"),
    ("paper", "read"),
    ("paper", "list"),
    ("paper", "recent"),
    ("paper", "trending"),
    ("paper", "related"),
    ("paper", "lineage", "list"),
    ("task",),
    ("task", "list"),
    ("method",),
    ("method", "list"),
    ("conference",),
    ("conference", "list"),
    ("organization",),
    ("organization", "list"),
    ("framework",),
    ("framework", "list"),
    ("benchmark",),
    ("benchmark", "list"),
)

# Flags that only shape terminal rendering. JSON output ignores them, so they
# have no in-process equivalent.
PRESENTATION_FLAGS = frozenset({"json", "implementation_coverage", "flat"})

_HIDDEN_ACTIONS = (
    argparse._SubParsersAction,
    argparse._HelpAction,
    argparse._VersionAction,
)


class _StrictParser(Parser):
    """Raise ``UsageError`` for invalid arguments instead of exiting."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def _command_label(command: Command) -> str:
    return " ".join(("pwc", *command))


def command_parser(
    command: Command, parser: argparse.ArgumentParser | None = None
) -> argparse.ArgumentParser:
    """Return the subparser that handles ``command``."""
    current = parser or build_parser(parser_class=_StrictParser)
    for name in command:
        subparsers = next(
            (
                action
                for action in current._actions
                if isinstance(action, argparse._SubParsersAction)
            ),
            None,
        )
        if subparsers is None or name not in subparsers.choices:
            raise KeyError(f"unknown command: {_command_label(command)}")
        current = subparsers.choices[name]
    return current


def query_options(command: Command) -> dict[str, argparse.Action]:
    """Research options of one command keyed by destination.

    Presentation-only flags, help, version, and nested command selectors are
    excluded, leaving exactly the options an embedder must expose.
    """
    parser = command_parser(command)
    return {
        action.dest: action
        for action in parser._actions
        if not isinstance(action, _HIDDEN_ACTIONS)
        and action.dest not in PRESENTATION_FLAGS
    }


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def build_argv(command: Command, options: Mapping[str, Any]) -> list[str]:
    """Translate structured options into the argv the CLI parser expects.

    ``None`` leaves a flag at its CLI default. Booleans toggle ``store_true``
    flags. Lists repeat ``append`` flags or join comma-separated values, and
    mappings expand to repeated ``NAME=VALUE`` entries.
    """
    actions = query_options(command)
    unknown = sorted(set(options) - set(actions))
    if unknown:
        raise UsageError(
            f"unknown option(s) for {_command_label(command)}: {', '.join(unknown)}"
        )
    argv = list(command)
    positionals: list[str] = []
    for dest, action in actions.items():
        value = options.get(dest)
        if value is None:
            continue
        if not action.option_strings:
            positionals.append(_text(value))
            continue
        flag = action.option_strings[0]
        if isinstance(action, argparse._StoreTrueAction):
            if value is True:
                argv.append(flag)
            elif value is not False:
                raise UsageError(f"{flag} expects a boolean")
            continue
        if isinstance(value, Mapping):
            value = [f"{name}={_text(item)}" for name, item in value.items()]
        values = list(value) if isinstance(value, (list, tuple, set)) else [value]
        if isinstance(action, argparse._AppendAction):
            for item in values:
                argv.extend((flag, _text(item)))
        elif values:
            argv.extend((flag, ",".join(_text(item) for item in values)))
    if positionals:
        argv.append("--")
        argv.extend(positionals)
    return argv


def query(command: Command, options: Mapping[str, Any], client: Any) -> Any:
    """Run one read-only CLI command in-process and return its JSON ``data``.

    ``client`` must provide ``get(path, params) -> Response`` like
    :class:`pwc_cli.transport.Client`. Invalid options raise ``UsageError``;
    catalog failures raise the CLI's ``TransportError`` or ``ResponseError``.
    """
    command = tuple(command)
    if command not in READ_ONLY_COMMANDS:
        raise UsageError(
            f"{_command_label(command)} is not a read-only research command"
        )
    argv = build_argv(command, options)
    args = build_parser(parser_class=_StrictParser).parse_args(argv)
    sink: list[Any] = []
    args.json = True
    args.result_sink = sink
    args.handler(args, client)
    if len(sink) != 1:
        raise ResponseError(
            f"{_command_label(command)} did not produce one JSON result"
        )
    return sink[0]
