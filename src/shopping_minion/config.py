"""Model configuration: one model per role (ADR-0009)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_MODELS_PATH = Path("config/models.yaml")

# OpenAI-compatible APIs (GLM, local servers) use provider "openai" plus `base_url`.
ChatProvider = Literal["anthropic", "openai", "google_genai", "ollama"]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatRole(_Config):
    provider: ChatProvider
    model: str
    base_url: str | None = None  # for OpenAI-compatible APIs such as GLM
    api_key_env: str | None = None  # name of the env var holding the key; never the key itself
    max_tokens: int | None = None
    fallback: ChatRole | None = None  # tried once when the primary fails (ADR-0011)

    @model_validator(mode="after")
    def _compatible_api_needs_key_env(self) -> ChatRole:
        if self.base_url and not self.api_key_env:
            raise ValueError("a role with base_url must name its api_key_env")
        if self.fallback and self.fallback.fallback:
            raise ValueError("only one level of fallback is supported")
        return self


class ResolverRole(_Config):
    backend: Literal["haiku", "julia1", "jev"]
    model: str | None = None
    provider: ChatProvider = "anthropic"  # LLM backends only
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens: int | None = None

    def chat_role(self) -> ChatRole:
        assert self.model is not None
        return ChatRole(
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            max_tokens=self.max_tokens,
        )

    @model_validator(mode="after")
    def _llm_backend_needs_model(self) -> ResolverRole:
        if self.backend == "haiku" and not self.model:
            raise ValueError("resolver backend 'haiku' requires a model")
        return self


class ModelsConfig(_Config):
    intake: ChatRole
    resolver: ResolverRole
    discovery: ChatRole


def load_models_config(path: Path = DEFAULT_MODELS_PATH) -> ModelsConfig:
    with path.open(encoding="utf-8") as f:
        return ModelsConfig.model_validate(yaml.safe_load(f))
