"""Preferences: the SQLite table (first imported from preferencias.yaml) and item matching."""

import logging
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from shopping_minion.items import Item, Quantity

if TYPE_CHECKING:
    from shopping_minion.storage import Storage

log = logging.getLogger(__name__)
IMPORT_MARK = "preferences_imported_at"


def read_yaml_preferences(path: str | Path) -> dict[str, dict]:
    """The entries of a preferencias.yaml, as written (an absent file gives {})."""
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(key): (entry or {}) for key, entry in data.items()}


def validate_entry(entry: dict) -> dict:
    """The entry as stored: a dict whose `quantidade`, when set, is {valor > 0, unidade} with a
    known unit (the same rule as `preference_quantity`). Raises ValueError otherwise."""
    if not isinstance(entry, dict):
        raise ValueError("a preferência deve ser um objeto")
    quantidade = entry.get("quantidade")
    if quantidade:
        try:
            Quantity(value=quantidade["valor"], unit=quantidade["unidade"])
        except (KeyError, TypeError, ValidationError) as exc:
            raise ValueError(
                "quantidade inválida: use valor maior que zero e uma unidade conhecida"
            ) from exc
    return entry


def import_yaml_once(storage: "Storage", yaml_path: str | Path) -> int:
    """First use: when the table is empty, was never imported and the file exists, copy the
    file's entries into the table and log it. Afterwards the table is the source and the file is
    left alone. Returns how many entries were imported (0 on every later call)."""
    if storage.read_preferences() or storage.get_meta(IMPORT_MARK) is not None:
        return 0
    entries = read_yaml_preferences(yaml_path)
    if not Path(yaml_path).exists():
        return 0
    for name, entry in entries.items():
        # `nome` keeps the name as written, since the key loses accents and case
        storage.set_preference(normalize_key(name), {"nome": name, **entry})
    storage.set_meta(IMPORT_MARK, datetime.now(UTC).isoformat(timespec="seconds"))
    log.info("preferences: imported %d entries from %s", len(entries), yaml_path)
    return len(entries)


def load_preferences(
    storage: "Storage", yaml_path: str | Path = "data/preferencias.yaml"
) -> dict[str, dict]:
    """The preferences table, after the one-time import of `yaml_path`."""
    import_yaml_once(storage, yaml_path)
    return storage.read_preferences()


def normalize_key(name: str) -> str:
    return _normalize(name)


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
