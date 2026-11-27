"""Serve the packaged PwC MCP skill through the Skills extension (SEP-2640)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml
from mcp.server.context import ServerRequestContext
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams

SKILLS_EXTENSION = "io.modelcontextprotocol/skills"
SKILL_NAME = "pwc-mcp"
SKILL_URI = f"skill://{SKILL_NAME}/SKILL.md"
# skills/list results carry ttlMs and cacheScope from this protocol version on.
LIST_CACHING_PROTOCOL = "2026-07-28"
# The packaged skill changes only with a release, like tools/list.
SKILLS_TTL_MS = 3_600_000


class SkillGetParams(RequestParams):
    uri: str


@dataclass(frozen=True)
class Skill:
    """One skill served as resources; its listing entry is fixed at startup."""

    uri: str
    text: str
    frontmatter: dict[str, Any]

    @classmethod
    def from_markdown(cls, uri: str, text: str) -> Skill:
        if not text.startswith("---\n"):
            raise ValueError(f"{uri} must begin with YAML frontmatter")
        frontmatter = yaml.safe_load(text.split("---\n", 2)[1])
        if not isinstance(frontmatter, dict) or not frontmatter.get("description"):
            raise ValueError(f"{uri} frontmatter needs a name and description")
        if uri.removesuffix("/SKILL.md").rsplit("/", 1)[-1] != frontmatter.get("name"):
            raise ValueError(f"{uri} must end with the frontmatter name")
        return cls(uri=uri, text=text, frontmatter=frontmatter)

    def entry(self) -> dict[str, Any]:
        content = self.text.encode("utf-8")
        return {
            "uri": self.uri,
            "frontmatter": self.frontmatter,
            "resources": [
                {
                    "uri": self.uri,
                    "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                    "size": len(content),
                }
            ],
        }

    def resource(self) -> TextResource:
        return TextResource(
            uri=self.uri,
            name=str(self.frontmatter["name"]),
            description=str(self.frontmatter["description"]),
            mime_type="text/markdown",
            text=self.text,
        )


def packaged_skill() -> Skill:
    text = (files("pwc_mcp") / "SKILL.md").read_text(encoding="utf-8")
    return Skill.from_markdown(SKILL_URI, text)


class SkillsExtension(Extension):
    """Advertise skills, serve skills/list and skills/get, and expose their files."""

    identifier = SKILLS_EXTENSION

    def __init__(self, skills: Sequence[Skill]) -> None:
        self._skills = {skill.uri: skill for skill in skills}

    def resources(self) -> Sequence[ResourceBinding]:
        return [ResourceBinding(resource=skill.resource()) for skill in self._skills.values()]

    def methods(self) -> Sequence[MethodBinding]:
        return [
            MethodBinding("skills/list", PaginatedRequestParams, self._list),
            MethodBinding("skills/get", SkillGetParams, self._get),
        ]

    async def _list(
        self, context: ServerRequestContext[Any, Any], _params: PaginatedRequestParams
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "resultType": "complete",
            "skills": [skill.entry() for skill in self._skills.values()],
        }
        if context.protocol_version >= LIST_CACHING_PROTOCOL:
            result |= {"ttlMs": SKILLS_TTL_MS, "cacheScope": "public"}
        return result

    async def _get(
        self, _context: ServerRequestContext[Any, Any], params: SkillGetParams
    ) -> dict[str, Any]:
        skill = self._skills.get(params.uri)
        if skill is None:
            raise MCPError(INVALID_PARAMS, f"Unknown skill: {params.uri}")
        return {"resultType": "complete", "skill": skill.entry()}
