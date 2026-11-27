from __future__ import annotations

import hashlib

import pytest
import yaml
from pwc_mcp.app import create_app
from pwc_mcp.skills import SKILL_URI, SKILLS_EXTENSION, Skill, packaged_skill
from starlette.testclient import TestClient
from test_server import StubCatalog

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _rpc(client, method, params, version=MODERN, name=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    meta = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}},
    )
    return response.json()


@pytest.fixture
def client():
    with TestClient(create_app(StubCatalog(), allowed_hosts=["testserver"])) as client:
        yield client


def test_discovery_advertises_skills_beside_resources(client):
    capabilities = _rpc(client, "server/discover", {})["result"]["capabilities"]

    assert capabilities["extensions"][SKILLS_EXTENSION] == {}
    assert "resources" in capabilities


def test_skill_listing_matches_the_served_file(client):
    listing = _rpc(client, "skills/list", {})["result"]
    read = _rpc(client, "resources/read", {"uri": SKILL_URI}, name=SKILL_URI)["result"]
    content = read["contents"][0]
    raw = content["text"].encode("utf-8")
    (entry,) = listing["skills"]

    assert listing["resultType"] == "complete"
    assert entry["uri"] == SKILL_URI
    assert entry["frontmatter"] == yaml.safe_load(content["text"].split("---\n", 2)[1])
    assert entry["frontmatter"]["name"] == "pwc-mcp"
    assert entry["resources"] == [
        {
            "uri": SKILL_URI,
            "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
            "size": len(raw),
        }
    ]
    assert content["mimeType"] == "text/markdown"


def test_list_caching_attributes_follow_the_protocol_version(client):
    modern = _rpc(client, "skills/list", {})["result"]
    legacy = _rpc(client, "skills/list", {}, version=LEGACY)["result"]

    assert (modern["ttlMs"], modern["cacheScope"]) == (3_600_000, "public")
    assert "ttlMs" not in legacy and "cacheScope" not in legacy
    assert legacy["skills"] == modern["skills"]


def test_skill_get_returns_the_entry_and_rejects_unknown_uris(client):
    found = _rpc(client, "skills/get", {"uri": SKILL_URI})["result"]
    missing = _rpc(client, "skills/get", {"uri": "skill://unknown/SKILL.md"})

    assert found["resultType"] == "complete"
    assert found["skill"] == packaged_skill().entry()
    assert missing["error"]["code"] == -32602


def test_skill_path_must_end_with_its_frontmatter_name():
    text = '---\nname: pwc-mcp\ndescription: "Search papers."\n---\nBody\n'

    assert Skill.from_markdown("skill://pwc-mcp/SKILL.md", text).frontmatter["name"] == "pwc-mcp"
    with pytest.raises(ValueError, match="frontmatter name"):
        Skill.from_markdown("skill://other/SKILL.md", text)
    with pytest.raises(ValueError, match="YAML frontmatter"):
        Skill.from_markdown("skill://pwc-mcp/SKILL.md", "Body\n")
