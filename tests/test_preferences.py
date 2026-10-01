from pathlib import Path

from shopping_minion.items import Item, Quantity
from shopping_minion.preferences import find_preference, load_preferences, preference_quantity

EXAMPLE = Path(__file__).parent.parent / "config" / "preferencias.exemplo.yaml"


def make_item(name: str, search_term: str | None = None) -> Item:
    return Item(source_line=name, name=name, search_term=search_term or name)


def test_missing_file_gives_empty_dict(tmp_path):
    assert load_preferences(tmp_path / "nope.yaml") == {}


def test_empty_file_gives_empty_dict(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("")
    assert load_preferences(path) == {}


def test_example_file_loads():
    prefs = load_preferences(EXAMPLE)
    assert set(prefs) == {"feijão", "presunto"}
    assert prefs["feijão"]["excluir"] == ["preto"]
    assert prefs["presunto"]["fatiado"] is True


def test_match_by_key_ignores_case_and_accents():
    prefs = load_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("FEIJAO")) is prefs["feijão"]
    assert find_preference(prefs, make_item("Feijão")) is prefs["feijão"]


def test_match_by_alias():
    prefs = load_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("feijao NORMAL")) is prefs["feijão"]


def test_match_by_search_term():
    prefs = load_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("fiambre", "Presunto")) is prefs["presunto"]


def test_no_match_returns_none():
    prefs = load_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("atum")) is None
    assert find_preference({}, make_item("atum")) is None


def test_entry_without_apelidos_still_matches_by_key():
    assert find_preference({"atum": {"marca": "Gomes"}}, make_item("Atum")) == {"marca": "Gomes"}


def test_preference_quantity():
    prefs = load_preferences(EXAMPLE)
    assert preference_quantity(prefs["presunto"]) == Quantity(value=300, unit="g")
    assert preference_quantity(prefs["feijão"]) == Quantity(value=1, unit="kg")


def test_preference_quantity_absent():
    assert preference_quantity({"marca": "x"}) is None
    assert preference_quantity(None) is None
