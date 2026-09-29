import pytest
from pydantic import ValidationError

from shopping_minion.config import ChatRole
from shopping_minion.models import chat_model_kwargs

GLM = ChatRole(
    provider="openai",
    model="glm-5.3",
    base_url="https://api.z.ai/api/paas/v4",
    api_key_env="ZAI_API_KEY",
)


def test_compatible_api_reads_key_from_named_env(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "k")
    kwargs = chat_model_kwargs(GLM)
    assert kwargs == {"model_provider": "openai", "base_url": GLM.base_url, "api_key": "k"}


def test_no_max_tokens_unless_configured(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "k")
    assert "max_tokens" not in chat_model_kwargs(GLM)
    assert (
        chat_model_kwargs(ChatRole(provider="anthropic", model="m", max_tokens=100))["max_tokens"]
        == 100
    )


@pytest.mark.parametrize("value", [None, "", "encrypted:abc"])
def test_missing_or_still_encrypted_key_is_a_clear_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ZAI_API_KEY", value)
    with pytest.raises(RuntimeError, match="dotenvx set ZAI_API_KEY"):
        chat_model_kwargs(GLM)


def test_base_url_requires_key_env_name():
    with pytest.raises(ValidationError, match="api_key_env"):
        ChatRole(provider="openai", model="glm-5.3", base_url="https://api.z.ai/api/paas/v4")
