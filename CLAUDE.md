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

All five milestones are implemented; README.md has a section per milestone.

## Commands

```bash
uv sync                 # install (creates .venv)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run pytest           # tests (tests/ only)
uv run --env-file .env python -m stagecraft.api   # HTTP + SSE on :8000
uv run --env-file .env python -m stagecraft.mcp   # MCP server on stdio
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
  db.py       Database: one connection + lock, nestable transactions (outermost commits)
  assets/     AssetStore: source identity, archive-as-record (DB triggers forbid restore and
              delete), resolve() as the one exit for name lookups, apply_turn_changes()
  memory/     turn_context.py (rebuilt per model call, lives in instructions, never stored),
              session_memory.py (SDK Session over SQLite, repair on read, maybe_compact),
              items.py (repair_history, estimate_tokens), compaction.py, long_term.py
  api/        app.py (routes, build_services), turns.py (TurnService: lease -> background run ->
              events), events.py (EventBus: bounded log + broadcast + seq), leases.py (SQLite TTL
              lease), sessions.py (sessions, messages, turns; client_message_id idempotency)
  mcp/        server.py (build_mcp_server: user-level tools only, turn events as progress),
              client.py (McpToolBridge: deny-by-default allowed_tools, <server>__<tool> names),
              __main__.py (stdio entry point; logs to stderr only)
  graph/      the same flow on LangGraph: workflow.py (StateGraph, interrupt, checkpointer),
              agents.py (GraphDeps and the node-level agents)
tests/        all tests; pytest runs nothing outside this directory
docs/         framework-comparison.md
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
- Everything a client sees goes through `EventBus.publish`. The panel is rebuilt from `state`
  events (full plan snapshots), never from assistant text.
- A turn that must stop for the user ends in code (`interrupt: true` in a tool result plus the
  orchestrator's `stop_on_interrupt`), not by asking the model to stop.
- HTTP tests run a real uvicorn server (`tests/conftest.py`): httpx's ASGI transport buffers
  whole responses and never returns for an event stream.
- `FakeModel` must stay a plain class: the SDK fingerprints dataclass models with `asdict`.
- Anything that looks an asset up by name calls `AssetStore.resolve` / `resolve_many`. Never
  query the assets table directly from a tool.
- Writes that must land together share a `Database` and nest `with db.transaction():`.
- Per-turn context belongs in `turn_context.py` (dynamic instructions), not in the user message
  and not in session memory.
- Renaming or removing a tool is safe for stored history (repair on read), but keep
  `ROLE_TOOLS` accurate: repair uses it to decide which calls are retired.
- The MCP server exposes what a *user* can do (sessions, messages, reading the plan). Never add
  an internal tool such as `plan_update_stage_state` to it: that would bypass review.
- External MCP tools are admitted only through `allowed_tools`, and a role must name them in
  `extra_role_tools` to hold them.
- This project uses the `mcp` 2.x SDK: `MCPServer` (not FastMCP) and `mcp.client.Client`.
- In the LangGraph version, code before `interrupt()` re-runs on resume: keep it idempotent,
  and commit state changes in an earlier node when they must be visible while waiting.
- Durable state lives in the Plan Store and the session tables, not in the transcript.
- One milestone = one commit, plus a README section explaining the problem, the design and
  the trade-offs in a form that can be spoken in two minutes.
- Commit messages: conventional prefix (`feat:`, `chore:`, `docs:`, `test:`), no AI tool
  attribution of any kind.
