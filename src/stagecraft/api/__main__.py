"""Serve the HTTP API.

    uv run --env-file .env python -m stagecraft.api     # a real model, data in .data/
    uv run python -m stagecraft.api --demo              # offline demo rules, data in .data/demo/

``--demo`` needs no key and makes no network call: every role runs ``DemoModel``, and reference
search runs on BM25 alone. Environment: ``HOST``, ``PORT``, ``STAGECRAFT_DATA_DIR``,
``STAGECRAFT_MEMORY_THRESHOLD_TOKENS`` (when session memory is compacted) and
``STAGECRAFT_DEMO_DELAY`` (seconds the demo pauses before each step, default 0.4).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

import uvicorn

from stagecraft.agents.demo_model import DemoCompactor, DemoRewriter, demo_models
from stagecraft.agents.runtime import same_model_for_all_roles
from stagecraft.agents.single import build_openai_model
from stagecraft.api.app import build_services, create_app
from stagecraft.config import MissingConfigError, ModelConfig
from stagecraft.memory import ModelCompactor, ModelRewriter
from stagecraft.tools.retrieval import DEFAULT_CORPUS_DIR, ReferenceIndex, reference_index_from_env


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stagecraft.api", description=__doc__)
    parser.add_argument(
        "--demo", action="store_true", help="run every role on the offline demo rules"
    )
    args = parser.parse_args(argv)

    data_dir = Path(os.environ.get("STAGECRAFT_DATA_DIR", ".data/demo" if args.demo else ".data"))
    threshold = int(os.environ.get("STAGECRAFT_MEMORY_THRESHOLD_TOKENS", "8000"))
    eval_dirs = {"local": Path(".data/evals"), "docs": Path("docs/evals")}
    if args.demo:
        delay = float(os.environ.get("STAGECRAFT_DEMO_DELAY", "0.4"))
        services = build_services(
            models_for=lambda _session_id: demo_models(delay=delay),
            data_dir=data_dir,
            compactor=DemoCompactor(),
            rewriter=DemoRewriter(),
            memory_threshold_tokens=threshold,
            references=ReferenceIndex(
                corpus_dir=os.environ.get("STAGECRAFT_CORPUS_DIR", DEFAULT_CORPUS_DIR)
            ),
            model_name="demo (offline rules)",
            eval_dirs=eval_dirs,
        )
    else:
        try:
            config = ModelConfig.from_env()
        except MissingConfigError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        model = build_openai_model(config)
        services = build_services(
            models_for=lambda _session_id: same_model_for_all_roles(model),
            data_dir=data_dir,
            compactor=ModelCompactor(model),
            rewriter=ModelRewriter(model),
            memory_threshold_tokens=threshold,
            references=reference_index_from_env(),
            model_name=f"{config.model} @ {urlparse(config.base_url).netloc or config.base_url}",
            eval_dirs=eval_dirs,
        )
    uvicorn.run(
        create_app(services),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
