"""An eval set for the agents: YAML cases, structured checks, a rubric judge, reports."""

from stagecraft.evals.cases import CATEGORIES, CaseLoadError, EvalCase, load_cases
from stagecraft.evals.checks import CheckResult, evaluate, grounded_ids
from stagecraft.evals.judge import RUBRIC_VERSION, JudgeInput, JudgeResult, ModelJudge
from stagecraft.evals.runner import CaseResult, SuiteMeta, SuiteResult, run_case, run_suite

__all__ = [
    "CATEGORIES",
    "RUBRIC_VERSION",
    "CaseLoadError",
    "CaseResult",
    "CheckResult",
    "EvalCase",
    "JudgeInput",
    "JudgeResult",
    "ModelJudge",
    "SuiteMeta",
    "SuiteResult",
    "evaluate",
    "grounded_ids",
    "load_cases",
    "run_case",
    "run_suite",
]
