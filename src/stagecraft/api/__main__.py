"""``uv run --env-file .env python -m stagecraft.api``: serve the API on a real model."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from stagecraft.agents.runtime import same_model_for_all_roles
from stagecraft.agents.single import build_openai_model
from stagecraft.api.app import build_services, create_app
from stagecraft.config import MissingConfigError


def main() -> int:
    try:
        model = build_openai_model()
    except MissingConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    services = build_services(
        models_for=lambda _session_id: same_model_for_all_roles(model),
        data_dir=Path(os.environ.get("STAGECRAFT_DATA_DIR", ".data")),
    )
    uvicorn.run(
        create_app(services),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
