"""LLM-as-judge with a fixed rubric, for what structure cannot check.

Structured checks decide whether the system did the right thing. The judge grades only how
the final reply communicates it, and only runs after the structured checks passed: a
well-written reply about a stage that never ran is still a failure.

The rubric is fixed and versioned. Changing a criterion or an anchor changes
``RUBRIC_VERSION``; reports record it, so scores from different rubrics are never compared.
The judge sees the system state as well as the conversation, which is what lets it grade
honesty ("the draft is ready") against facts instead of against the reply's own tone.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from agents import Agent, Model, ModelSettings, RunConfig, Runner
from pydantic import BaseModel, Field

from stagecraft.agents.submit import build_submit_tool, read_submission, stop_on_submit
from stagecraft.tools.registry import ToolRegistry
from stagecraft.tools.results import ToolResult

RUBRIC_VERSION = "reply-rubric-v1"
PASS_MIN_SCORE = 3
PASS_MEAN_SCORE = 4.0

CRITERIA: dict[str, str] = {
    "addresses_request": (
        "The reply is about what the user asked for: the deliverables, format, tone and count "
        "they named. 5: fully. 3: partly; something requested is missing or something "
        "unrequested was added. 1: about something else."
    ),
    "clear_next_step": (
        "The user can tell what happened and what, if anything, is needed from them now "
        "(approve, answer a question, nothing). 5: explicit. 3: inferable with effort. "
        "1: unclear or contradictory."
    ),
    "honest_status": (
        "Every claim about progress or produced items matches the system state given below. "
        "5: all claims match. 3: vague but nothing false. 1: claims work the state does not "
        "show, or hides a failure."
    ),
    "concise": (
        "No filler, repetition or raw internal payloads such as JSON or full tool results. "
        "5: tight. 3: some padding. 1: dominated by padding or dumps."
    ),
}

JUDGE_INSTRUCTIONS = """\
You grade the final assistant reply of a chat with a staged content pipeline.

Your input is JSON: the conversation (user messages and assistant replies, oldest first), the
system state after the last reply (the plan with each stage's state, open questions, produced
artefacts) and the tools called in the last turn. The system state is the truth. The replies
are what the user saw.

Score each criterion from 1 to 5 using its anchors. Grade only the last reply, using the
earlier conversation as context. Do not reward length. Then call submit_verdict with the four
scores and a rationale of at most two sentences. Do not answer with text.

Criteria:
"""


Score = Annotated[int, Field(ge=1, le=5)]


class JudgeVerdict(ToolResult):
    addresses_request: Score
    clear_next_step: Score
    honest_status: Score
    concise: Score
    rationale: str


class JudgeInput(BaseModel):
    conversation: list[dict[str, str]]
    plan: str | None
    open_questions: list[str]
    artefacts: list[str]
    last_turn_tools: list[str]


class JudgeResult(BaseModel):
    rubric: str = RUBRIC_VERSION
    scores: dict[str, int]
    rationale: str
    passed: bool

    @property
    def mean(self) -> float:
        return sum(self.scores.values()) / len(self.scores)


class Judge(Protocol):
    model_name: str

    async def grade(self, payload: JudgeInput) -> JudgeResult: ...


def verdict_result(verdict: JudgeVerdict) -> JudgeResult:
    scores = {name: int(getattr(verdict, name)) for name in CRITERIA}
    mean = sum(scores.values()) / len(scores)
    return JudgeResult(
        scores=scores,
        rationale=verdict.rationale,
        passed=min(scores.values()) >= PASS_MIN_SCORE and mean >= PASS_MEAN_SCORE,
    )


class ModelJudge:
    """Grades with any model through the same submit-tool pattern the sub-agents use."""

    SUBMIT_TOOL = "submit_verdict"

    def __init__(self, model: Model, *, model_name: str = "judge", max_turns: int = 4) -> None:
        self.model = model
        self.model_name = model_name
        self.max_turns = max_turns
        self._registry = ToolRegistry(
            [
                build_submit_tool(
                    JudgeVerdict,
                    name=self.SUBMIT_TOOL,
                    description="Submit your scores and rationale. This ends your run.",
                )
            ]
        )

    async def grade(self, payload: JudgeInput) -> JudgeResult:
        agent = Agent(
            name="judge",
            instructions=JUDGE_INSTRUCTIONS + render_rubric(),
            model=self.model,
            tools=self._registry.select(self.SUBMIT_TOOL),
            tool_use_behavior=stop_on_submit(self.SUBMIT_TOOL),
            model_settings=ModelSettings(temperature=0),
        )
        result = await Runner.run(
            agent,
            payload.model_dump_json(),
            max_turns=self.max_turns,
            run_config=RunConfig(tracing_disabled=True),
        )
        return verdict_result(read_submission(result, JudgeVerdict, role="judge"))


def render_rubric() -> str:
    return "\n".join(f"- {name}: {anchors}" for name, anchors in CRITERIA.items())
