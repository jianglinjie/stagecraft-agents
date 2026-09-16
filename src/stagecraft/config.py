"""Model endpoint configuration.

Every model call goes through an OpenAI-compatible endpoint chosen by environment
variables, so any provider that speaks the Chat Completions API works. Tests never
read these: they use the scripted fake model instead.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is absent."""


class ModelConfig(BaseModel):
    """Where to send model calls and which model to ask for."""

    model_config = ConfigDict(frozen=True)

    base_url: str = DEFAULT_BASE_URL
    api_key: str
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ModelConfig:
        source = os.environ if env is None else env
        api_key = source.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise MissingConfigError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and fill in a key for any "
                "OpenAI-compatible endpoint."
            )
        return cls(
            base_url=source.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            api_key=api_key,
            model=source.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        )
