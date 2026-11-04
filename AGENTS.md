# Repository Agent Instructions

- Install the MCP runtime with `uv sync --project mcp_server --no-dev`.
- Run the MCP server with `uv run --project mcp_server pwc-mcp`.
- Local runtime smokes bind a temporary loopback port and supply an ephemeral `PWC_MCP_CURSOR_KEY_CURRENT`; never use production cursor credentials.
