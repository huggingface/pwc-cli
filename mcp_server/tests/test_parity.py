"""Every read-only CLI command is a tool, and every research flag a parameter."""

from __future__ import annotations

import argparse
import asyncio

from mcp.client import Client
from pwc_cli import queries
from pwc_mcp.server import (
    ENTITY_PARAMETERS,
    MCP_ONLY_PARAMETERS,
    PARAMETER_NAMES,
    TOOL_COMMANDS,
    build_server,
    cli_options,
)
from test_server import StubCatalog

# Deliberate default divergences from the CLI, documented in README.md.
DEFAULT_EXCEPTIONS = {}


def _tools():
    async def exercise():
        async with Client(build_server(StubCatalog())) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    return asyncio.run(exercise())


def _enum_values(schema, root):
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    values = list(schema.get("enum") or [])
    for branch in schema.get("anyOf") or []:
        values.extend(_enum_values(branch, root))
    return values


def _mcp_name(tool: str, destination: str) -> str:
    if destination == "name":
        return ENTITY_PARAMETERS[tool]
    return PARAMETER_NAMES.get(destination, destination)


def test_every_read_only_cli_command_has_exactly_one_tool():
    assert sorted(TOOL_COMMANDS.values()) == sorted(queries.READ_ONLY_COMMANDS)
    assert len(set(TOOL_COMMANDS.values())) == len(TOOL_COMMANDS)


def test_every_cli_research_flag_is_a_tool_parameter_and_vice_versa():
    tools = _tools()

    assert set(tools) == set(TOOL_COMMANDS)
    for tool, command in TOOL_COMMANDS.items():
        cli = queries.query_options(command)
        schema = tools[tool].input_schema
        parameters = set(schema["properties"]) - MCP_ONLY_PARAMETERS.get(
            tool, frozenset()
        )
        mapped = cli_options(tool, **dict.fromkeys(parameters))

        assert set(mapped) == set(cli), tool
        for destination, action in cli.items():
            parameter = _mcp_name(tool, destination)
            property_schema = schema["properties"][parameter]
            if isinstance(action, argparse._StoreTrueAction):
                assert property_schema.get("type") == "boolean", (tool, parameter)
                expected = DEFAULT_EXCEPTIONS.get((tool, parameter), False)
                assert property_schema.get("default") is expected, (tool, parameter)
            elif action.choices and set(action.choices) != {"true", "false"}:
                assert set(action.choices) <= set(
                    _enum_values(property_schema, schema)
                ), (
                    tool,
                    parameter,
                )
            if not action.option_strings or destination == "name":
                assert parameter in schema["required"], (tool, parameter)


def test_paper_references_and_entity_names_are_required_everywhere():
    tools = _tools()

    for tool, entity in ENTITY_PARAMETERS.items():
        assert tools[tool].input_schema["required"] == [entity]
    for tool in (
        "get_paper_info",
        "get_paper_evaluations",
        "read_paper",
        "get_related_papers",
        "get_paper_lineage",
    ):
        assert tools[tool].input_schema["required"] == ["paper"]
