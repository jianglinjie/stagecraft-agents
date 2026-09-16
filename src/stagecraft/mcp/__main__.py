"""``python -m stagecraft.mcp``: serve the product over MCP on stdio with a real model."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from stagecraft.agents.runtime import same_model_for_all_roles
from stagecraft.agents.single import build_openai_model
from stagecraft.api.app import build_services
from stagecraft.config import MissingConfigError
from stagecraft.mcp.server import build_mcp_server
from stagecraft.memory import ModelCompactor, ModelRewriter
from stagecraft.tools.retrieval import reference_index_from_env


def main() -> int:
    # stdout is the protocol channel: every log line must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        model = build_openai_model()
    except MissingConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    services = build_services(
        models_for=lambda _session_id: same_model_for_all_roles(model),
        data_dir=Path(os.environ.get("STAGECRAFT_DATA_DIR", ".data")),
        compactor=ModelCompactor(model),
        rewriter=ModelRewriter(model),
        references=reference_index_from_env(),
    )
    build_mcp_server(services).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
