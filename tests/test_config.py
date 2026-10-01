from pathlib import Path

import pytest
from pydantic import ValidationError

from shopping_minion.config import DecideConfig, load_decide_config

REPO_CONFIG = Path(__file__).parent.parent / "config" / "decide.yaml"


def test_repo_config_loads():
    config = load_decide_config(REPO_CONFIG)
    assert config.model == "jev-1.13"
    assert config.batch_size == 5
    assert config.accept_at == 0.8
    assert config.ask_below == 0.5


def test_load_from_custom_path(tmp_path):
    path = tmp_path / "decide.yaml"
    path.write_text("model: x\nbatch_size: 1\naccept_at: 0.9\nask_below: 0.9\n")
    assert load_decide_config(path).batch_size == 1


def test_ask_below_cannot_exceed_accept_at():
    with pytest.raises(ValidationError):
        DecideConfig(model="x", batch_size=1, accept_at=0.5, ask_below=0.6)


@pytest.mark.parametrize("field", ["accept_at", "ask_below"])
@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_thresholds_must_be_in_unit_interval(field, value):
    data = {"model": "x", "batch_size": 1, "accept_at": 1.0, "ask_below": 0.0}
    data[field] = value
    with pytest.raises(ValidationError):
        DecideConfig(**data)


def test_batch_size_at_least_one():
    with pytest.raises(ValidationError):
        DecideConfig(model="x", batch_size=0, accept_at=0.8, ask_below=0.5)


def test_unknown_key_is_rejected(tmp_path):
    path = tmp_path / "decide.yaml"
    path.write_text("model: x\nbatch_size: 1\naccept_at: 0.8\nask_below: 0.5\nextra: 1\n")
    with pytest.raises(ValidationError):
        load_decide_config(path)
