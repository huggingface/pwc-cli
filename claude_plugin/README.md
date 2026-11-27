# Papers with Code for Claude

This plugin connects Claude to the public [Papers with Code](https://paperswithcode.co)
catalog of AI and machine learning research. Ask Claude to find papers on a
topic or by an author, read a paper's metadata and Markdown, find related work,
follow-up work, and predecessors, browse tasks, methods, conferences,
organizations, and frameworks, or compare models on a benchmark leaderboard,
with filters such as model size and metric thresholds.

## What it contains

- **MCP server** `paperswithcode`: the hosted, read-only Papers with Code MCP
  server at `https://paperswithcode.co/mcp`. It needs no account, API key, or
  sign-in.
- **Skill** `pwc-mcp`: instructions that teach Claude when to use each tool,
  how to pass paper, task, and benchmark references, and how to page through
  long results. The same skill is served by the MCP server through Skills over
  MCP.

## Usage

Install the plugin, then ask research questions in plain language, for example:

- "What are the newest papers on speculative decoding with official code?"
- "Show the best models under 3B parameters on MMLU-Pro."
- "Which papers build on arXiv 1706.03762?"

In Claude Code you can also add this repository as a marketplace:

```bash
/plugin marketplace add huggingface/pwc-cli
/plugin install paperswithcode@paperswithcode
```

## Data handling

Every tool call sends its arguments (search queries, paper identifiers, task,
benchmark, and filter names) to `https://paperswithcode.co/mcp`, which Hugging
Face operates. The server sees your IP address and uses it to enforce anonymous
rate limits. The plugin sends no conversation content beyond those tool
arguments, stores nothing on your machine, and runs no local code.

## License

Apache-2.0. Source: [huggingface/pwc-cli](https://github.com/huggingface/pwc-cli).
