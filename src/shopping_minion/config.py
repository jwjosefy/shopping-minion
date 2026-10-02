"""Loads config/decide.yaml (LLD section 3.4)."""

from pathlib import Path

import yaml
from pydantic import Field, model_validator

from shopping_minion.items import Contract


class DecideConfig(Contract):
    model: str
    batch_size: int = Field(ge=1)
    accept_at: float = Field(ge=0, le=1)
    ask_below: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "DecideConfig":
        if self.ask_below > self.accept_at:
            raise ValueError("ask_below must be <= accept_at")
        return self


def load_decide_config(path: str | Path = "config/decide.yaml") -> DecideConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DecideConfig.model_validate(data)


class HistoryConfig(Contract):
    first_sync_orders: int = Field(ge=1, le=10)  # the list's first page has 10 orders (T12)
    related_lines: int = Field(ge=1)  # k for the "related" lines of an item (LLD-M4 section 4.3)


def load_history_config(path: str | Path = "config/history.yaml") -> HistoryConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return HistoryConfig.model_validate(data)
