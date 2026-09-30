"""Preferences: hand-written YAML keyed by canonical item name (ADR-0003)."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PREFERENCES_PATH = Path("data/preferences.yaml")


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class DefaultQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float = Field(gt=0)
    unit: str = "unit"


class ItemPreference(BaseModel):
    """Known keys are typed; anything else (variant, sliced, ply...) is passed to the resolver."""

    model_config = ConfigDict(extra="allow", frozen=True)

    aliases: list[str] = Field(default_factory=list)
    brand: str | None = None
    exclude: list[str] = Field(default_factory=list)
    default_quantity: DefaultQuantity | float | None = None

    def hints(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True, exclude={"aliases"})
        return {k: v for k, v in data.items() if v not in ([], {}, "")}


class Preferences:
    def __init__(self, entries: dict[str, ItemPreference]) -> None:
        self._entries = entries
        self._by_alias = {
            _norm(alias): key
            for key, pref in entries.items()
            for alias in [key.replace("_", " "), *pref.aliases]
        }

    @classmethod
    def load(cls, path: Path = DEFAULT_PREFERENCES_PATH) -> Preferences:
        if not path.exists():
            return cls({})
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls({key: ItemPreference.model_validate(value or {}) for key, value in raw.items()})

    def find(self, item_name: str) -> tuple[str, ItemPreference] | None:
        key = self._by_alias.get(_norm(item_name))
        return (key, self._entries[key]) if key else None
