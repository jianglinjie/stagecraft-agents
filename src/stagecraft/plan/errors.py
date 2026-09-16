"""Plan-domain refusals.

Every one of these carries the code the model will see. They exist so that
"you may not do that" reaches the model as a fact it can act on, rather than as
a stack trace that ends the turn.
"""

from __future__ import annotations

from stagecraft.tools.results import StructuredToolError


class PlanError(StructuredToolError):
    """Base class for everything the Plan Store refuses."""


class PlanNotFound(PlanError):
    code = "not_found"


class RoleDenied(PlanError):
    code = "role_denied"


class RevisionConflict(PlanError):
    code = "revision_conflict"


class IllegalTransition(PlanError):
    code = "illegal_transition"


class ConfirmationRequired(PlanError):
    code = "confirmation_required"


class ContractRequired(PlanError):
    code = "contract_required"


class ResultsRequired(PlanError):
    code = "results_required"
