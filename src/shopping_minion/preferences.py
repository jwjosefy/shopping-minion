"""Loads data/preferencias.yaml and matches items to entries (LLD section 3.6)."""

import unicodedata
from pathlib import Path

import yaml

from shopping_minion.items import Item, Quantity


def load_preferences(path: str | Path = "data/preferencias.yaml") -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(key): (entry or {}) for key, entry in data.items()}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def find_preference(prefs: dict[str, dict], item: Item) -> dict | None:
    wanted = {_normalize(item.name), _normalize(item.search_term)}
    for key, entry in prefs.items():
        names = [key, *(entry.get("apelidos") or [])]
        if wanted & {_normalize(str(name)) for name in names}:
            return entry
    return None


def preference_quantity(entry: dict | None) -> Quantity | None:
    quantidade = (entry or {}).get("quantidade")
    if not quantidade:
        return None
    return Quantity(value=quantidade["valor"], unit=quantidade["unidade"])
