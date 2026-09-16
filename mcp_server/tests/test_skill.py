from __future__ import annotations

import re
from pathlib import Path

from pwc_mcp.server import TOOL_COMMANDS

SKILL = Path(__file__).parents[1] / "SKILL.md"
EXPECTED_TOOLS = set(TOOL_COMMANDS)


def test_mcp_skill_has_valid_agent_skills_frontmatter() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---\n", 2)[1]

    name = re.search(r"^name: ([a-z0-9-]+)$", frontmatter, re.MULTILINE)
    description = re.search(r'^description: "(.+)"$', frontmatter, re.MULTILINE)
    compatibility = re.search(r'^compatibility: "(.+)"$', frontmatter, re.MULTILINE)

    assert name is not None and name.group(1) == "pwc-mcp"
    assert description is not None and 1 <= len(description.group(1)) <= 1024
    assert compatibility is not None and len(compatibility.group(1)) <= 500


def test_mcp_skill_documents_every_public_tool_without_inventing_tools() -> None:
    text = SKILL.read_text(encoding="utf-8")
    tools_section = text.split("## Tools\n", 1)[1].split("## Research workflow", 1)[0]
    documented = set(re.findall(r"^- `([a-z_]+)\(", tools_section, re.MULTILINE))

    assert documented == EXPECTED_TOOLS
    assert re.search(r"Do not invent equivalent\s+tools", tools_section)


def test_mcp_skill_explains_paper_continuation_contract() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "next_cursor" in text
    assert "Treat the cursor as opaque" in text
    assert "same `paper`" in text
    assert "within one hour" in text
