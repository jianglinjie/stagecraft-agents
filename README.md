# stagecraft-agents

Multi-agent orchestration for staged content production, in Python: dispatch-and-return,
a shared plan store with a state machine, resumable turns with human-in-the-loop, SSE with
replay, turn leases, memory compaction, MCP, and a LangGraph rewrite for comparison.

The domain is deliberately generic (brief → outline → draft → render) and every tool is a
fake, so the repo is about the mechanisms, not the content.

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Tool registry + single agent | planned |
| 2 | Four roles + dispatch-and-return + Plan Store | planned |
| 3 | Resumable turns, human-in-the-loop, SSE, leases | planned |
| 4 | Memory, compaction, asset pool | planned |
| 5 | MCP server/client + LangGraph comparison | planned |

## Running

```bash
uv sync
cp .env.example .env      # any OpenAI-compatible endpoint works
uv run pytest             # tests use a fake model, no network
```

## Layout

```text
src/stagecraft/{agents,plan,tools,memory,api,mcp}
tests/
docs/
```
