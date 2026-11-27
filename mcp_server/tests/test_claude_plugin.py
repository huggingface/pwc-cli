from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).parents[2]
PLUGIN = REPOSITORY / "claude_plugin"


def test_plugin_skill_matches_served_skill() -> None:
    bundled = (PLUGIN / "skills/pwc-mcp/SKILL.md").read_text(encoding="utf-8")
    served = (REPOSITORY / "mcp_server/src/pwc_mcp/SKILL.md").read_text(encoding="utf-8")

    assert bundled == served


def test_plugin_version_tracks_mcp_server() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    project = tomllib.loads((REPOSITORY / "mcp_server/pyproject.toml").read_text(encoding="utf-8"))

    assert manifest["version"] == project["project"]["version"]


def test_plugin_connects_to_hosted_server() -> None:
    config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))

    assert config["mcpServers"]["paperswithcode"] == {
        "type": "http",
        "url": "https://paperswithcode.co/mcp",
    }
