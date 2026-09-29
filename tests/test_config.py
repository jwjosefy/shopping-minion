from pathlib import Path

import pytest
from pydantic import ValidationError

from shopping_minion.config import ModelsConfig, load_models_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_models_config_loads():
    config = load_models_config(REPO_ROOT / "config" / "models.yaml")
    assert config.intake.provider in {"anthropic", "openai", "google_genai", "ollama"}
    assert config.resolver.backend in {"haiku", "julia1", "jev"}


def test_llm_resolver_backend_requires_model():
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(
            {
                "intake": {"provider": "anthropic", "model": "m"},
                "resolver": {"backend": "haiku"},
                "discovery": {"provider": "anthropic", "model": "m"},
            }
        )


def test_decision_backend_without_model_is_fine():
    config = ModelsConfig.model_validate(
        {
            "intake": {"provider": "anthropic", "model": "m"},
            "resolver": {"backend": "julia1"},
            "discovery": {"provider": "anthropic", "model": "m"},
        }
    )
    assert config.resolver.model is None
