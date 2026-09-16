"""The Plan Store domain: models, state machine, persistence. It owns no tools."""

from stagecraft.plan.errors import (
    ConfirmationRequired,
    ContractRequired,
    IllegalTransition,
    PlanError,
    PlanNotFound,
    ResultsRequired,
    RevisionConflict,
    RoleDenied,
)
from stagecraft.plan.model import (
    Plan,
    ReviewKind,
    RuntimeRef,
    Stage,
    StageContract,
    StageRuntime,
    StageState,
    WorkItem,
)
from stagecraft.plan.state_machine import TERMINAL, TRANSITIONS, check_transition, next_action
from stagecraft.plan.store import WRITE_PERMISSIONS, PlanStore

__all__ = [
    "TERMINAL",
    "TRANSITIONS",
    "WRITE_PERMISSIONS",
    "ConfirmationRequired",
    "ContractRequired",
    "IllegalTransition",
    "Plan",
    "PlanError",
    "PlanNotFound",
    "PlanStore",
    "ResultsRequired",
    "ReviewKind",
    "RevisionConflict",
    "RoleDenied",
    "RuntimeRef",
    "Stage",
    "StageContract",
    "StageRuntime",
    "StageState",
    "WorkItem",
    "check_transition",
    "next_action",
]
