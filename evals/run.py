"""Run the eval set: ``uv run --env-file .env python evals/run.py --help``."""

from stagecraft.evals.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
