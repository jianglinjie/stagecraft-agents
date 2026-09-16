"""Model endpoint configuration.

Every model call goes through an OpenAI-compatible endpoint chosen by environment
variables, so any provider that speaks the Chat Completions API works. Tests never
read these: they use the scripted fake model instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


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


class EmbeddingConfig(BaseModel):
    """Where reference search gets vectors. Defaults to the chat endpoint's settings.

    Not every chat endpoint serves embeddings (DeepSeek does not), so ``EMBEDDING_BASE_URL``,
    ``EMBEDDING_API_KEY`` and ``EMBEDDING_MODEL`` can point somewhere else.
    ``EMBEDDING_MODEL=none`` turns vectors off; search then runs on BM25 alone.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str
    api_key: str
    model: str = DEFAULT_EMBEDDING_MODEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EmbeddingConfig | None:
        source = os.environ if env is None else env
        model = source.get("EMBEDDING_MODEL", "").strip() or DEFAULT_EMBEDDING_MODEL
        if model.lower() in {"none", "off"}:
            return None
        api_key = (source.get("EMBEDDING_API_KEY") or source.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return None
        base_url = (
            source.get("EMBEDDING_BASE_URL") or source.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        ).strip()
        return cls(base_url=base_url, api_key=api_key, model=model)
