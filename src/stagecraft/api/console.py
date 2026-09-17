"""Read-only views for the developer console in ``web/``.

``app.py`` is the product surface: what a user can do. These routes show a developer what is
behind it, so each mechanism can be exercised and inspected from a browser:

GET /console/info                      which model serves turns, the reference index, eval dirs
GET /console/tools                     the registry: every tool's schema and the roles holding it
GET /console/references?q=&top_k=      the planner's search, pointers and summaries only
GET /console/sessions/{id}/context     the Turn Context, session memory and topic profile
GET /console/evals                     eval result files found in the configured directories
GET /console/evals/{source}/{label}    one eval run's results and its markdown report

Nothing here writes, runs a model or calls a tool on anyone's behalf.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from stagecraft.agents.orchestrator import chat_session_key
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.runtime import build_runtime
from stagecraft.evals.runner import SuiteResult
from stagecraft.memory import estimate_tokens
from stagecraft.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from stagecraft.api.app import Services

LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RECENT_ITEMS = 12


def build_console_router(services: Services) -> APIRouter:
    router = APIRouter(prefix="/console", tags=["console"])

    @cache
    def registry() -> ToolRegistry:
        # A throwaway runtime, built once: the registry is the same for every session.
        return build_runtime(chat_id="console", models={}, references=services.references).registry

    @router.get("/info")
    async def info() -> dict[str, object]:
        return {
            "model": services.model_name,
            "references": asdict(services.references.stats()),
            "memory_threshold_tokens": services.memory_threshold_tokens,
            "eval_sources": {name: str(path) for name, path in services.eval_dirs.items()},
        }

    @router.get("/tools")
    async def tools() -> dict[str, object]:
        holders: dict[str, list[str]] = {}
        for role, names in ROLE_TOOLS.items():
            for name in names:
                holders.setdefault(name, []).append(role)
        return {
            "roles": {role: list(names) for role, names in ROLE_TOOLS.items()},
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.params_json_schema,
                    "roles": holders.get(spec.name, []),
                }
                for spec in registry()
            ],
        }

    @router.get("/references")
    async def references(
        q: str = "", top_k: Annotated[int, Query(ge=1, le=8)] = 5
    ) -> dict[str, object]:
        stats = await services.references.load()
        hits, mode = await services.references.search(q, top_k) if q.strip() else ([], stats.mode)
        return {
            "query": q,
            "mode": mode,
            "stats": asdict(stats),
            "hits": [hit.model_dump() for hit in hits],
        }

    @router.get("/sessions/{session_id}/context")
    async def session_context(session_id: str) -> dict[str, object]:
        record = services.sessions.get_session(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session not found")
        runtime = services.runtime_for(session_id)
        memory = runtime.memory(chat_session_key(session_id), "orchestrator")
        items = await memory.get_items()
        repair = memory.last_repair
        profile = services.long_term.get(record.topic) if record.topic else None
        return {
            "turn_context": runtime.turn_context().render(),
            "memory": {
                "items": len(items),
                "estimated_tokens": estimate_tokens(items),
                "threshold_tokens": memory.threshold_tokens,
                "compactions": memory.compactions(),
                "repair": {
                    "retired_tools": sorted(repair.retired_tools),
                    "interrupted_calls": list(repair.interrupted_calls),
                    "orphan_outputs": list(repair.orphan_outputs),
                },
                "recent": [_preview(item) for item in items[-RECENT_ITEMS:]],
            },
            "topic": record.topic,
            "profile": profile.model_dump() if profile else None,
        }

    @router.get("/evals")
    async def evals() -> dict[str, object]:
        reports: list[dict[str, object]] = []
        for source, directory in services.eval_dirs.items():
            paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
            for path in paths:
                suite = _read_suite(path)
                if suite is None:
                    continue
                counts = Counter(result.status for result in suite.results)
                reports.append(
                    {
                        "source": source,
                        "label": path.stem,
                        "started_at": suite.meta.started_at,
                        "model": suite.meta.model,
                        "judge_model": suite.meta.judge_model,
                        "cases": suite.meta.cases,
                        "repeat": suite.meta.repeat,
                        "runs": len(suite.results),
                        "passed": counts["passed"],
                        "failed": counts["failed"],
                        "errors": counts["error"],
                    }
                )
        return {
            "sources": {name: str(path) for name, path in services.eval_dirs.items()},
            "reports": reports,
        }

    @router.get("/evals/{source}/{label}")
    async def eval_report(source: str, label: str) -> dict[str, object]:
        directory = services.eval_dirs.get(source)
        path = directory / f"{label}.json" if directory and LABEL.fullmatch(label) else None
        suite = _read_suite(path) if path is not None and path.is_file() else None
        if path is None or suite is None:
            raise HTTPException(status_code=404, detail="report not found")
        markdown = path.with_suffix(".md")
        return {
            "source": source,
            "label": label,
            "suite": suite.model_dump(mode="json"),
            "markdown": markdown.read_text(encoding="utf-8") if markdown.is_file() else None,
        }

    return router


def _read_suite(path: Path) -> SuiteResult | None:
    try:
        return SuiteResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None  # not an eval results file


def _preview(item: Any) -> dict[str, str]:
    """One stored conversation item, reduced to what a person scanning the memory needs."""
    if not isinstance(item, dict):
        return {"kind": "other", "text": _clip(str(item))}
    if item.get("type") == "function_call":
        return {"kind": "tool_call", "text": f"{item.get('name')}({_clip(item.get('arguments'))})"}
    if item.get("type") == "function_call_output":
        return {"kind": "tool_output", "text": _clip(item.get("output"))}
    content = item.get("content")
    if isinstance(content, list):
        content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return {"kind": str(item.get("role", "other")), "text": _clip(content)}


def _clip(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
