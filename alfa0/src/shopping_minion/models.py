"""Build a LangChain chat model for a configured role (ADR-0009)."""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from shopping_minion.config import ChatRole


def chat_model_kwargs(role: ChatRole) -> dict[str, object]:
    kwargs: dict[str, object] = {"model_provider": role.provider}
    if role.max_tokens is not None:
        kwargs["max_tokens"] = role.max_tokens
    if role.base_url:
        kwargs["base_url"] = role.base_url
    if role.api_key_env:
        key = os.environ.get(role.api_key_env)
        if not key or key.startswith("encrypted:"):
            raise RuntimeError(
                f"{role.api_key_env} is not set; add it with `dotenvx set {role.api_key_env} ...` "
                "and run through `dotenvx run --`"
            )
        kwargs["api_key"] = key
    return kwargs


def chat_model(role: ChatRole) -> BaseChatModel:
    return init_chat_model(role.model, **chat_model_kwargs(role))
