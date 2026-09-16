# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`stagecraft-agents` reimplements the core mechanisms of a multi-agent content-production
platform in Python, for learning and interview demonstration. The business domain is a
generic **staged content pipeline** (brief → outline → draft → render). Every tool is a fake
implementation. Nothing in this repo may reference a real company, product, prompt or dataset.

The mechanisms being reproduced, one per milestone:

1. Tool registry + single agent — schema generated from function signatures, used both as the
   model-facing description and as runtime validation.
2. Four roles (orchestrator / router / planner / executor) + dispatch-and-return + Plan Store —
   sub-agents receive an explicit payload only; planner and executor never talk directly.
3. Resumable turns + human-in-the-loop — planner resumes by task id, asks questions and
   interrupts; FastAPI sessions, SSE with replay, idempotent message ids, turn leases.
4. Memory + assets — persisted session memory with compaction; asset pool with source
   dedup, archive-as-record, single rejection path.
5. MCP server/client + a LangGraph rewrite of the same topology with a comparison doc.

## Commands

```bash
uv sync                 # install (creates .venv)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run pytest           # tests (tests/ only)
```

Lint and tests must be green before every commit.

## Layout

```text
src/stagecraft/
  config.py   ModelConfig from OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
  agents/     orchestrator / router / planner / executor, one file each;
              roles.py is the role -> tool-name table (the only role assembly);
              runtime.py builds the registry and starts sub-agent runs;
              dispatch.py holds the three dispatch_* tools and the submit_* tools;
              submit.py explains why sub-agents return through a tool, not output_type;
              fake_model.py is the scripted Model used by every test
  plan/       model.py (Plan, Stage: contract vs runtime), state_machine.py, store.py
              (SQLite, revision CAS, role guard), errors.py (codes the model sees)
  tools/      registry.py (@tool, ToolSpec, ToolRegistry), results.py (ToolResult / ToolError /
              StructuredToolError), context.py (RunContext: the injected caller role),
              fake/ (FakeWorkspace + four content tools), plan/ (five plan tools)
  memory/     session memory, compaction, long-term memory interface
  api/        FastAPI app, SSE, event bus, leases
  mcp/        MCP server (FastMCP) and client
tests/        all tests; pytest runs nothing outside this directory
docs/         design notes and comparisons
```

## Conventions

- Python 3.12, `uv` for deps, `ruff` (line length 100), `pytest` with `asyncio_mode=auto`,
  `pydantic` v2 for every model and tool schema.
- Model calls go through the OpenAI-compatible API configured by `OPENAI_BASE_URL` and
  `OPENAI_API_KEY`. Tests never hit the network: use the programmable fake model in
  `stagecraft.agents.fake_model`.
- Tool return values carry only what the model needs for its next decision (status, ids,
  summaries, suggested next step), never raw payloads.
- A tool that needs to know its caller declares a `RunContext` parameter. It is injected from
  the run and never appears in the schema. Never add a `role` argument a model could fill in.
- Domain refusals raise a `StructuredToolError` subclass with a code and a hint; the registry
  turns it into a `ToolError`. Only unexpected exceptions become `tool_failed`.
- Sub-agents return results by calling their `submit_*` tool (see `agents/submit.py`). Do not
  switch them to `output_type`: it breaks endpoints without json_schema response formats.
- Durable state lives in the Plan Store and the session tables, not in the transcript.
- One milestone = one commit, plus a README section explaining the problem, the design and
  the trade-offs in a form that can be spoken in two minutes.
- Commit messages: conventional prefix (`feat:`, `chore:`, `docs:`, `test:`), no AI tool
  attribution of any kind.
