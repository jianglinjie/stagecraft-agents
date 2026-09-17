"""An offline stand-in for a model: fixed rules per role, so the console runs without an endpoint.

``DemoModel`` implements the SDK's ``Model`` interface like ``FakeModel`` does, but instead of
replaying a script it works out each step from what a real model would be shown on that call:
the instructions (which carry the Turn Context) and the conversation items (the user's message
or a sub-agent's payload, then the tool results so far). The rules follow the prompts and the
``next_action`` facts in tool results, and they read keywords, not meaning.

It lets every mechanism be exercised from a browser with no key: routing, the Plan Store and
its review gate, interrupts, dispatch payloads, SSE, idempotency, leases, assets and cited
references. It says nothing about how well a model follows the prompts; evals and live checks
still need a real endpoint.

    uv run python -m stagecraft.api --demo
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from pydantic import BaseModel

from stagecraft.agents.fake_model import Reply, ToolCall, reply, tool_call
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.memory.items import is_user_message
from stagecraft.tools.context import Role

Step = ToolCall | Reply

# -- what the model is shown ----------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    """A tool result from the current run, with the call that produced it."""

    name: str
    arguments: dict[str, Any]
    output: dict[str, Any]

    @property
    def failed(self) -> bool:
        return self.output.get("status") == "error"


@dataclass(frozen=True)
class View:
    instructions: str
    items: list[dict[str, Any]]

    @classmethod
    def of(cls, instructions: str | None, input: str | list[TResponseInputItem]) -> View:
        if isinstance(input, str):
            return cls(instructions or "", [{"role": "user", "content": input}])
        return cls(instructions or "", [_as_dict(item) for item in input])

    @property
    def request(self) -> str:
        """The latest user message: the chat message, or a sub-agent's JSON payload."""
        index = self._request_index()
        return _text(self.items[index].get("content")) if index is not None else ""

    def results(self) -> list[Result]:
        """Tool results since the latest user message, oldest first."""
        index = self._request_index()
        calls: dict[str, tuple[str, dict[str, Any]]] = {}
        found: list[Result] = []
        for item in self.items[0 if index is None else index + 1 :]:
            if item.get("type") == "function_call":
                arguments = _json(item.get("arguments")) or {}
                calls[str(item.get("call_id"))] = (str(item.get("name")), arguments)
            elif item.get("type") == "function_call_output":
                name, arguments = calls.get(str(item.get("call_id")), ("", {}))
                found.append(Result(name, arguments, _json(item.get("output")) or {}))
        return found

    def section(self, title: str) -> str:
        """The body of the Turn Context's ``### title`` section, or an empty string."""
        pattern = rf"^### {re.escape(title)}\n(.*?)(?=^### |\Z)"
        match = re.search(pattern, self.instructions, re.M | re.S)
        return match.group(1).strip() if match else ""

    def _request_index(self) -> int | None:
        for index in range(len(self.items) - 1, -1, -1):
            if is_user_message(self.items[index]):
                return index
        return None


@dataclass(frozen=True)
class StageRow:
    order: int
    state: str
    id: str
    goal: str


@dataclass(frozen=True)
class PlanRows:
    """The plan as the Turn Context summarises it: one row per stage."""

    id: str
    objective: str
    stages: list[StageRow]
    questions: list[str]

    def first(self, state: str) -> StageRow | None:
        return next((stage for stage in self.stages if stage.state == state), None)

    def stage(self, stage_id: str) -> StageRow | None:
        return next((stage for stage in self.stages if stage.id == stage_id), None)


PLAN_HEADER = re.compile(r"^plan (plan_\d+) \(rev \d+\)(?:: no stages yet| — (.*))$", re.M)
STAGE_ROW = re.compile(r"^\s+(\d+)\. \[(\w+)\] (\S+) — (.*)$", re.M)
QUESTIONS_ROW = re.compile(r"^\s+open questions: (.*)$", re.M)


def parse_plan(summary: str) -> PlanRows | None:
    header = PLAN_HEADER.search(summary)
    if header is None:
        return None
    questions = QUESTIONS_ROW.search(summary)
    return PlanRows(
        id=header.group(1),
        objective=header.group(2) or "",
        stages=[StageRow(int(o), s, i, g) for o, s, i, g in STAGE_ROW.findall(summary)],
        questions=questions.group(1).split(" | ") if questions else [],
    )


# -- keywords ---------------------------------------------------------------------------------

URL = re.compile(r"https?://[^\s，。）)]+")
WORKFLOW_CUE = re.compile(
    r"\b(series|two-part|three-part|multi-part|campaign|several|multiple|stages?|plan|review)\b"
    r"|系列|计划|阶段|审核|多篇"
)
APPROVE = re.compile(
    r"\b(approved?|yes|ok(ay)?|lgtm|go ahead|continue|proceed|looks good|sounds good)\b"
    r"|批准|同意|可以|继续|没问题|好的|确认"
)
NEGATION = re.compile(r"\b(not|don'?t|no)\b|不要|不批准|不同意|别")
SKIP = re.compile(r"\b(skip|omit)\b|跳过|省略")
ARCHIVE = re.compile(r"\barchive\b|\bdon'?t use\b|\bstop using\b|归档|别用|不要用|弃用")
NEW_WORK = re.compile(r"\b(write|create|make|produce)\b|写|生成|制作")
AUDIENCES = {
    "developers": r"\b(developers?|engineers?|programmers?)\b|开发者|工程师|程序员",
    "beginners": r"\b(beginners?|newcomers?|new to)\b|新手|入门|初学者",
    "managers": r"\b(managers?|executives?|decision makers?)\b|管理者|经理|决策者",
}
FORMAT_QUERIES = {
    "html": "HTML article structure and headings",
    "pdf": "PDF datasheet specification table",
    "video": "short video length pacing captions",
}
CONTENT_KINDS = {
    "fetch_brief": "brief",
    "write_outline": "outline",
    "write_draft": "draft",
    "render_output": "render",
}
ID_FIELDS = {
    "brief": "brief_id",
    "outline": "outline_id",
    "draft": "draft_id",
    "render": "render_id",
}
KIND_WORDS = {
    "brief": r"brief|简报",
    "outline": r"outline|大纲",
    "draft": r"draft|草稿|撰写",
    "render": r"render|渲染|导出",
}


def source_of(text: str) -> str:
    """A URL in the text, else what it is "about" or "from", else the text itself."""
    match = URL.search(text)
    if match:
        return match.group(0).rstrip(".,;:!?")
    said = re.search(r"\b(?:about|from) (.+?)(?=\s+(?:for|as|in|with)\b|[,.;]|$)", text)
    return _line(said.group(1) if said else text, 80)


def tone_of(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(playful|fun|casual|light-hearted)\b|活泼|轻松|有趣", lower):
        return "playful"
    if re.search(r"\bformal\b|正式|严谨", lower):
        return "formal"
    return "neutral"


def format_of(text: str) -> str:
    lower = text.lower()
    if re.search(r"\bpdf\b|datasheet", lower):
        return "pdf"
    if re.search(r"\bvideo\b|视频", lower):
        return "video"
    return "html"


def audience_of(text: str) -> str | None:
    lower = text.lower()
    return next((name for name, cue in AUDIENCES.items() if re.search(cue, lower)), None)


def sections_of(text: str) -> int:
    match = re.search(r"(\d+)\s*(?:sections?|个部分|部分|节)", text.lower())
    return min(max(int(match.group(1)), 1), 10) if match else 3


def approves(text: str) -> bool:
    lower = text.lower()
    return bool(APPROVE.search(lower)) and not NEGATION.search(lower)


# -- orchestrator -----------------------------------------------------------------------------


def orchestrator_step(view: View) -> Step:
    text = view.request
    plan = parse_plan(view.section("Plan"))
    results = view.results()
    if not results:
        return _open_turn(view, text, plan)
    last = results[-1]
    if last.failed:
        return reply(_refusal(last))
    follow = _AFTER.get(last.name)
    return follow(view, text, plan, results) if follow else reply(f"{last.name} finished.")


def _open_turn(view: View, text: str, plan: PlanRows | None) -> Step:
    lower = text.lower()
    if plan is not None and plan.questions:
        return tool_call("dispatch_planner", plan_id=plan.id, goal=plan.objective, answers=[text])
    named = _mentioned(text, _asset_names(view.section("Active assets")))
    if named and ARCHIVE.search(lower):
        return tool_call("archive_session_assets", names=named, reason="user_request")
    waiting = plan.first("waiting_user") if plan else None
    if plan is not None and waiting is not None and not URL.search(text):
        if SKIP.search(lower):
            return _move(plan.id, waiting.id, "omitted")
        if approves(text):
            return _move(plan.id, waiting.id, "doing", user_confirmed=True)
        if not NEW_WORK.search(lower):
            return reply(
                f"{waiting.id} ({waiting.goal}) is waiting for your review. "
                "Reply “approve” to start it, or “skip” to leave it out."
            )
    blocked = plan.first("blocked") if plan else None
    if plan is not None and blocked is not None and SKIP.search(lower):
        return _move(plan.id, blocked.id, "omitted")
    if approves(text) and not URL.search(text) and not NEW_WORK.search(lower):
        doing = plan.first("doing") if plan else None
        if plan is not None and doing is not None:
            return _execute(plan.id, doing)
        if plan is not None and blocked is not None:
            return _move(plan.id, blocked.id, "doing")
        return reply("Nothing is waiting for your approval. Tell me what to make.")
    return tool_call("dispatch_router", request=text)


def _after_router(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    if results[-1].output["route"] == "direct":
        return tool_call("fetch_brief", source=source_of(text))
    return tool_call("plan_create", objective=_line(text, 160))


def _after_brief(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    return tool_call(
        "write_outline", brief_id=results[-1].output["brief_id"], sections=sections_of(text)
    )


def _after_outline(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    active = _asset_names(view.section("Active assets"))
    added = _asset_names(view.section("Added with this message"))
    assets = [name for name in active if name in added or name in _mentioned(text, active)]
    return tool_call(
        "write_draft",
        outline_id=results[-1].output["outline_id"],
        tone=tone_of(text),
        reference_assets=assets,
    )


def _after_draft(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    return tool_call(
        "render_output", draft_id=results[-1].output["draft_id"], format=format_of(text)
    )


def _after_render(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    chain = [
        result.output[ID_FIELDS[CONTENT_KINDS[result.name]]]
        for result in results
        if result.name in CONTENT_KINDS and not result.failed
    ]
    draft = next((r for r in results if r.name == "write_draft" and not r.failed), None)
    used = draft.output.get("reference_assets", []) if draft else []
    answer = f"Done: {' → '.join(chain)} ({results[-1].output['format']})."
    if used:
        answer += f" The draft draws on {', '.join(used)}."
    return reply(answer + _panel_note(view))


def _after_plan_create(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    return tool_call("dispatch_planner", plan_id=results[-1].output["plan_id"], goal=text)


def _after_planner(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    pending = plan.first("pending") if plan else None
    if plan is None or pending is None:
        return reply(f"The planner wrote no stage to review: {results[-1].output['summary']}")
    return _move(plan.id, pending.id, "waiting_user", review_kind="plan_review")


def _after_state(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    last = results[-1]
    plan_id, stage_id, state = last.output["plan_id"], last.output["stage_id"], last.output["state"]
    if state == "waiting_user":
        return tool_call("plan_get_stage_detail", plan_id=plan_id, stage_id=stage_id)
    row = plan.stage(stage_id) if plan else None
    if state == "doing" and row is not None:
        return _execute(plan_id, row)
    if state in ("done", "omitted"):
        pending = plan.first("pending") if plan else None
        if pending is not None:
            return _move(plan_id, pending.id, "waiting_user", review_kind="plan_review")
        return reply(" ".join([*_stage_reports(results), "All stages are finished."]))
    reason = last.arguments.get("blocked_reason") or "no reason given"
    return reply(f"{stage_id} is {state}: {reason}.")


def _after_detail(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    detail = results[-1].output
    contract = detail.get("contract") or {}
    total = len(plan.stages) if plan else "?"
    lines = [
        *_stage_reports(results),
        f"Stage {detail['order']} of {total} ({detail['stage_id']}) needs your review before it "
        f"starts: {detail['goal']}",
        *(
            f"- {item['id']} {item['name']}: {item['instruction']}"
            for item in contract.get("work_items", [])
        ),
    ]
    if contract.get("sources"):
        lines.append("Sources: " + ", ".join(contract["sources"]))
    if contract.get("acceptance"):
        lines.append(f"Acceptance: {contract['acceptance']}")
    lines.append("Reply “approve” to start it, or “skip” to leave it out.")
    return reply("\n".join(lines) + _panel_note(view))


def _after_executor(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    out = results[-1].output
    plan_id, stage_id, pending = out["plan_id"], out["stage_id"], out["pending_items"]
    if not pending:
        return _move(plan_id, stage_id, "done")
    attempts = sum(
        1 for r in results if r.name == "dispatch_executor" and r.output.get("stage_id") == stage_id
    )
    row = plan.stage(stage_id) if plan else None
    if attempts < 2 and row is not None:
        return _execute(plan_id, row, retry_ids=pending)
    return tool_call(
        "plan_update_stage_state",
        plan_id=plan_id,
        stage_id=stage_id,
        target="blocked",
        blocked_reason=f"work items {', '.join(pending)} did not finish after a retry",
    )


def _after_archive(view: View, text: str, plan: PlanRows | None, results: list[Result]) -> Step:
    archived = results[-1].output["archived"]
    return reply(
        f"Archived {', '.join(archived) or 'nothing new'}. Archiving is final: to use that "
        "material again, add it as a new asset."
    )


_AFTER: dict[str, Callable[[View, str, PlanRows | None, list[Result]], Step]] = {
    "dispatch_router": _after_router,
    "fetch_brief": _after_brief,
    "write_outline": _after_outline,
    "write_draft": _after_draft,
    "render_output": _after_render,
    "plan_create": _after_plan_create,
    "dispatch_planner": _after_planner,
    "plan_update_stage_state": _after_state,
    "plan_get_stage_detail": _after_detail,
    "dispatch_executor": _after_executor,
    "archive_session_assets": _after_archive,
}


def _move(plan_id: str, stage_id: str, target: str, **extra: Any) -> ToolCall:
    return tool_call(
        "plan_update_stage_state", plan_id=plan_id, stage_id=stage_id, target=target, **extra
    )


def _execute(plan_id: str, stage: StageRow, retry_ids: list[str] | None = None) -> ToolCall:
    extra = {"retry_ids": retry_ids} if retry_ids else {}
    return tool_call(
        "dispatch_executor",
        plan_id=plan_id,
        stage_id=stage.id,
        order=stage.order,
        goal=stage.goal,
        **extra,
    )


def _stage_reports(results: list[Result]) -> list[str]:
    reports: list[str] = []
    for result in results:
        if result.name == "dispatch_executor" and not result.failed and result.output["refs"]:
            refs = ", ".join(ref["ref_id"] for ref in result.output["refs"])
            reports.append(f"{result.output['stage_id']} produced {refs}.")
        elif result.name == "plan_update_stage_state" and result.output.get("state") == "omitted":
            reports.append(f"{result.output['stage_id']} was skipped.")
    return reports


def _refusal(result: Result) -> str:
    code, message, hint = (result.output.get(key) for key in ("code", "message", "hint"))
    return f"{result.name} was refused ({code}): {message}" + (f" {hint}" if hint else "")


def _panel_note(view: View) -> str:
    notes = [
        line[2:]
        for line in view.section("Archived by the user with this message").splitlines()
        if line.startswith("- ")
    ]
    return "\n\nArchived from the panel: " + " ".join(notes) if notes else ""


def _asset_names(section: str) -> list[str]:
    return re.findall(r"^- (.+?) \([^()]*\)(?::|$)", section, re.M)


def _mentioned(text: str, names: list[str]) -> list[str]:
    lower = text.lower()
    return [name for name in names if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", lower)]


# -- router, planner, executor ----------------------------------------------------------------


def router_step(view: View) -> Step:
    if view.results():
        return reply("The routing submission was refused.")
    payload = _json(view.request) or {}
    request = str(payload.get("request", view.request))
    cue = WORKFLOW_CUE.search(request.lower())
    if cue:
        reason = f"It mentions “{cue.group(0)}”: staged work the user reviews."
        return tool_call("submit_route", route="workflow", reason=reason)
    return tool_call("submit_route", route="direct", reason="One piece, produced in one pass.")


@dataclass(frozen=True)
class StageSpec:
    goal: str
    query: str
    items: tuple[tuple[str, str, str], ...]
    acceptance: str


def planner_step(view: View) -> Step:
    payload = _json(view.request) or {}
    plan_id = str(payload.get("plan_id", ""))
    answers = [str(answer) for answer in payload.get("answers") or []]
    wanted = " ".join([str(payload.get("goal", "")), *answers])
    results = view.results()
    if results and results[-1].name == "submit_plan":
        return reply("The plan submission was refused.")
    if results and results[-1].failed:
        return _submit_plan(f"Stopped: {results[-1].output.get('message')}", done=False)

    # An answer that only approves ("ok, continue") does not say who the audience is: ask again.
    named = [answer for answer in answers if not approves(answer)]
    audience = audience_of(wanted) or (_line(named[-1], 40) if named else None)
    if audience is None:
        return _submit_plan(
            "Planning needs the audience first.",
            questions=["Who is this for: developers, beginners or managers?"],
            done=False,
        )
    specs = _stage_specs(str(payload.get("goal", "")), wanted, audience)
    index, writing = divmod(len(results), 2)  # search, write, search, write, ...
    if index == len(specs):
        return _submit_plan(f"{len(specs)} stages: " + "; ".join(spec.goal for spec in specs))
    spec = specs[index]
    if not writing:
        return tool_call("search_references", query=spec.query, top_k=2)
    hits = results[-1].output.get("hits", [])
    return tool_call(
        "plan_write_stage_contract",
        plan_id=plan_id,
        goal=spec.goal,
        work_items=[{"id": i, "name": n, "instruction": text} for i, n, text in spec.items],
        inputs=[results[-2].output["stage_id"]] if index else [],
        acceptance=spec.acceptance,
        sources=[hit["pointer"] for hit in hits[:1]],
    )


def _stage_specs(goal: str, wanted: str, audience: str) -> list[StageSpec]:
    source, tone, fmt, sections = (
        source_of(goal),
        tone_of(wanted),
        format_of(wanted),
        sections_of(wanted),
    )
    return [
        StageSpec(
            goal=f"Brief and outline for {audience}",
            query=f"writing for {audience}",
            items=(
                ("w1", "brief", f"Fetch the brief from {source}"),
                ("w2", "outline", f"Outline it in {sections} sections for {audience}"),
            ),
            acceptance=f"an outline with {sections} sections",
        ),
        StageSpec(
            goal=f"A {tone} draft",
            query=f"{tone} tone voice rules" if tone != "neutral" else "plain language",
            items=(("w1", "draft", f"Write a {tone} draft from the outline"),),
            acceptance="a draft that follows the outline",
        ),
        StageSpec(
            goal=f"Render as {fmt}",
            query=FORMAT_QUERIES[fmt],
            items=(("w1", "render", f"Render the draft as {fmt}"),),
            acceptance=f"a {fmt} render of the draft",
        ),
    ]


def _submit_plan(summary: str, *, questions: list[str] | None = None, done: bool = True) -> Step:
    return tool_call("submit_plan", summary=summary, questions=questions or [], done_authoring=done)


def executor_step(view: View) -> Step:
    payload = _json(view.request) or {}
    plan_id, stage_id = str(payload.get("plan_id", "")), str(payload.get("stage_id", ""))
    retry = set(payload.get("retry_ids") or [])
    results = view.results()
    if not results:
        return tool_call("plan_get_stage_detail", plan_id=plan_id, stage_id=stage_id)
    if results[-1].name == "submit_execution":
        return reply("The execution submission was refused.")
    detail = results[0].output
    if results[0].failed:
        return _submit_execution(f"Could not read {stage_id}: {detail.get('message')}", [])

    wanted = [
        item
        for item in (detail.get("contract") or {}).get("work_items", [])
        if not retry or item["id"] in retry
    ]
    doable = [item for item in wanted if _kind(item)]
    undoable = [item["id"] for item in wanted if not _kind(item)]
    work = results[1:]
    if work and work[-1].name == "plan_attach_runtime":
        produced = work[:-1]
        failed = [
            item["id"]
            for item, result in zip(doable, produced, strict=True)
            if result.failed or work[-1].failed
        ]
        summary = f"{len(doable) - len(failed)} of {len(wanted)} work items finished."
        return _submit_execution(summary, failed + undoable)
    if len(work) < len(doable):
        return _content_call(doable[len(work)], detail, work)

    refs = [
        {
            "ref_id": result.output[ID_FIELDS[kind]],
            "kind": kind,
            "summary": result.output["summary"],
            "work_item_id": item["id"],
        }
        for item, result in zip(doable, work, strict=True)
        if not result.failed and (kind := CONTENT_KINDS.get(result.name))
    ]
    if not refs:
        return _submit_execution("No work item finished.", [item["id"] for item in wanted])
    return tool_call("plan_attach_runtime", plan_id=plan_id, stage_id=stage_id, refs=refs)


def _kind(item: dict[str, Any]) -> str | None:
    """Which content tool a work item needs: by its name, else by its instruction.

    The name goes first because an instruction mentions its inputs too ("render the draft").
    """
    for text in (str(item.get("name", "")), str(item.get("instruction", ""))):
        lower = text.lower()
        found = next((kind for kind, cue in KIND_WORDS.items() if re.search(cue, lower)), None)
        if found:
            return found
    return None


def _content_call(item: dict[str, Any], detail: dict[str, Any], work: list[Result]) -> ToolCall:
    kind = _kind(item)
    text = f"{item['name']}: {item['instruction']}"
    available = [
        *detail.get("upstream_refs", []),
        *detail.get("runtime_refs", []),
        *(
            {"ref_id": r.output[ID_FIELDS[CONTENT_KINDS[r.name]]], "kind": CONTENT_KINDS[r.name]}
            for r in work
            if r.name in CONTENT_KINDS and not r.failed
        ),
    ]

    def latest(wanted: str) -> str:
        found = [ref["ref_id"] for ref in available if ref["kind"] == wanted]
        return found[-1] if found else f"no_{wanted}"

    if kind == "brief":
        return tool_call("fetch_brief", source=source_of(item["instruction"]))
    if kind == "outline":
        return tool_call("write_outline", brief_id=latest("brief"), sections=sections_of(text))
    if kind == "draft":
        return tool_call("write_draft", outline_id=latest("outline"), tone=tone_of(text))
    return tool_call("render_output", draft_id=latest("draft"), format=format_of(text))


def _submit_execution(summary: str, failed_item_ids: list[str]) -> Step:
    return tool_call("submit_execution", summary=summary, failed_item_ids=failed_item_ids)


POLICIES: dict[Role, Callable[[View], Step]] = {
    "orchestrator": orchestrator_step,
    "router": router_step,
    "planner": planner_step,
    "executor": executor_step,
}


# -- the Model ----------------------------------------------------------------------------------


class DemoModel(Model):
    """Answers each call with the next step of its role's rules.

    ``delay`` pauses before every answer and between streamed words, so a browser can watch a
    turn unfold and a second send can arrive while one is running. A plain class, like
    ``FakeModel``: the SDK fingerprints dataclass models with ``asdict``.
    """

    def __init__(self, role: Role, *, delay: float = 0.0) -> None:
        self.role = role
        self.delay = delay
        self.policy = POLICIES[role]

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        if self.delay:
            await asyncio.sleep(self.delay)
        step = self.policy(View.of(system_instructions, input))
        return ModelResponse(
            output=[_output_item(step)], usage=Usage(), response_id=f"demo_{uuid.uuid4().hex}"
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        response = await self.get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        sequence = 0
        for output_index, item in enumerate(response.output):
            if not isinstance(item, ResponseOutputMessage):
                continue
            for content_index, part in enumerate(item.content):
                if not isinstance(part, ResponseOutputText):
                    continue
                for chunk in re.findall(r"\S+\s*|\s+", part.text):
                    if self.delay:
                        await asyncio.sleep(self.delay / 20)
                    yield ResponseTextDeltaEvent(
                        type="response.output_text.delta",
                        item_id=item.id,
                        output_index=output_index,
                        content_index=content_index,
                        delta=chunk,
                        logprobs=[],
                        sequence_number=sequence,
                    )
                    sequence += 1
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=sequence,
            response=Response(
                id=response.response_id or "demo_resp",
                created_at=time.time(),
                model="demo",
                object="response",
                output=response.output,
                parallel_tool_calls=True,
                tool_choice="auto",
                tools=[],
            ),
        )


def demo_models(*, delay: float = 0.0) -> dict[Role, Model]:
    return {role: DemoModel(role, delay=delay) for role in ROLE_TOOLS}


class DemoCompactor:
    """Summarises without a model: the user's requests and the ids produced, in order."""

    async def __call__(self, items: list[Any]) -> str:
        requests = [
            _line(_text(item.get("content")), 80) for item in items if is_user_message(item)
        ]
        ids = dict.fromkeys(ARTEFACT_ID.findall(json.dumps(items, ensure_ascii=False, default=str)))
        return (
            f"The user asked: {' / '.join(requests) or 'nothing yet'}. "
            f"Ids so far: {', '.join(ids) or 'none'}."
        )


class DemoRewriter:
    """Rewrites a topic profile without a model, from what the user said in the session.

    A later session's audience, tone or format replaces the earlier one rather than being added
    next to it, which is what rewriting instead of appending is for.
    """

    async def __call__(self, current: str, transcript: str) -> str:
        profile: dict[str, str] = {}
        for line in current.splitlines():
            key, sep, value = line.removeprefix("- ").partition(":")
            if line.startswith("- ") and sep:
                profile[key.strip()] = value.strip()
        said = "\n".join(
            line.removeprefix("user: ")
            for line in transcript.splitlines()
            if line.startswith("user: ")
        )
        tone, fmt = tone_of(said), format_of(said)
        learned = {
            "Audience": audience_of(said),
            "Tone": tone if tone != "neutral" else None,
            "Format": fmt if fmt != "html" or re.search(r"\bhtml\b", said.lower()) else None,
        }
        profile.update({key: value for key, value in learned.items() if value})
        return "\n".join(f"- {k}: {v}" for k, v in profile.items()) or "- Nothing durable yet."


ARTEFACT_ID = re.compile(r"\b(?:plan|brief|outline|draft|render)_\d{4}\b|\bstage_\d{2}\b")


# -- helpers ------------------------------------------------------------------------------------


def _output_item(step: Step) -> Any:
    item_id = f"demo_{uuid.uuid4().hex[:16]}"
    if isinstance(step, ToolCall):
        return ResponseFunctionToolCall(
            id=item_id,
            call_id=item_id,
            type="function_call",
            name=step.name,
            arguments=json.dumps(step.arguments, ensure_ascii=False),
        )
    return ResponseOutputMessage(
        id=item_id,
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=step.text, annotations=[])],
    )


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json", exclude_none=True)
    return {}


def _json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict)).strip()
    return ""


def _line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
