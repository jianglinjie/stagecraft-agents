"""Which tools each role holds. Roles pick by name; they never build a tool.

This table is the whole of role assembly. Adding a capability to a role is one
name in one tuple, and a role cannot reach a tool that is not listed here.
"""

from __future__ import annotations

from stagecraft.tools.context import Role

CONTENT_TOOLS: tuple[str, ...] = ("fetch_brief", "write_outline", "write_draft", "render_output")

ROLE_TOOLS: dict[Role, tuple[str, ...]] = {
    "orchestrator": (
        "dispatch_router",
        "dispatch_planner",
        "dispatch_executor",
        "plan_create",
        "plan_get_stage_detail",
        "plan_update_stage_state",
        *CONTENT_TOOLS,
    ),
    "router": ("submit_route",),
    "planner": ("plan_get_stage_detail", "plan_write_stage_contract", "submit_plan"),
    "executor": (
        "plan_get_stage_detail",
        "plan_attach_runtime",
        *CONTENT_TOOLS,
        "submit_execution",
    ),
}
