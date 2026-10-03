from pathlib import Path

from shopping_minion.items import Item, Quantity
from shopping_minion.preferences import (
    find_preference,
    load_preferences,
    normalize_key,
    preference_quantity,
    read_yaml_preferences,
)
from shopping_minion.storage import Storage

EXAMPLE = Path(__file__).parent.parent / "config" / "preferencias.exemplo.yaml"


def make_item(name: str, search_term: str | None = None) -> Item:
    return Item(source_line=name, name=name, search_term=search_term or name)


def test_missing_file_gives_empty_dict(tmp_path):
    assert read_yaml_preferences(tmp_path / "nope.yaml") == {}


def test_empty_file_gives_empty_dict(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("")
    assert read_yaml_preferences(path) == {}


def test_example_file_loads():
    prefs = read_yaml_preferences(EXAMPLE)
    assert set(prefs) == {"feijão", "presunto"}
    assert prefs["feijão"]["excluir"] == ["preto"]
    assert prefs["presunto"]["fatiado"] is True


def test_match_by_key_ignores_case_and_accents():
    prefs = read_yaml_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("FEIJAO")) is prefs["feijão"]
    assert find_preference(prefs, make_item("Feijão")) is prefs["feijão"]


def test_match_by_alias():
    prefs = read_yaml_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("feijao NORMAL")) is prefs["feijão"]


def test_match_by_search_term():
    prefs = read_yaml_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("fiambre", "Presunto")) is prefs["presunto"]


def test_no_match_returns_none():
    prefs = read_yaml_preferences(EXAMPLE)
    assert find_preference(prefs, make_item("atum")) is None
    assert find_preference({}, make_item("atum")) is None


def test_entry_without_apelidos_still_matches_by_key():
    assert find_preference({"atum": {"marca": "Gomes"}}, make_item("Atum")) == {"marca": "Gomes"}


def test_preference_quantity():
    prefs = read_yaml_preferences(EXAMPLE)
    assert preference_quantity(prefs["presunto"]) == Quantity(value=300, unit="g")
    assert preference_quantity(prefs["feijão"]) == Quantity(value=1, unit="kg")


def test_preference_quantity_absent():
    assert preference_quantity({"marca": "x"}) is None
    assert preference_quantity(None) is None


def test_first_load_imports_the_file_into_the_table(tmp_path, caplog):
    db = Storage(tmp_path / "t.sqlite")
    with caplog.at_level("INFO", logger="shopping_minion.preferences"):
        prefs = load_preferences(db, EXAMPLE)
    assert set(prefs) == {"feijao", "presunto"}
    assert prefs["feijao"]["nome"] == "feijão"
    assert prefs["feijao"]["excluir"] == ["preto"]
    assert db.read_preferences() == prefs
    assert "imported 2 entries" in caplog.text


def test_the_import_happens_once(tmp_path, caplog):
    db = Storage(tmp_path / "t.sqlite")
    load_preferences(db, EXAMPLE)
    db.delete_preference("presunto")
    with caplog.at_level("INFO", logger="shopping_minion.preferences"):
        prefs = load_preferences(db, EXAMPLE)
    assert set(prefs) == {"feijao"}  # the table is the source, the file is left alone
    assert caplog.text == ""


def test_removing_every_entry_does_not_bring_the_file_back(tmp_path):
    db = Storage(tmp_path / "t.sqlite")
    load_preferences(db, EXAMPLE)
    db.delete_preference("feijao")
    db.delete_preference("presunto")
    assert load_preferences(db, EXAMPLE) == {}


def test_a_non_empty_table_is_not_imported_over(tmp_path):
    db = Storage(tmp_path / "t.sqlite")
    db.set_preference("atum", {"marca": "Gomes"})
    assert load_preferences(db, EXAMPLE) == {"atum": {"marca": "Gomes"}}


def test_a_missing_file_is_fine_and_a_later_one_is_still_imported(tmp_path):
    db = Storage(tmp_path / "t.sqlite")
    assert load_preferences(db, tmp_path / "nope.yaml") == {}
    path = tmp_path / "p.yaml"
    path.write_text("atum:\n  marca: Gomes\n", encoding="utf-8")
    assert set(load_preferences(db, path)) == {"atum"}


def test_find_preference_matches_the_table_by_name_and_apelidos(tmp_path):
    db = Storage(tmp_path / "t.sqlite")
    prefs = load_preferences(db, EXAMPLE)
    assert find_preference(prefs, make_item("Feijão")) is prefs["feijao"]
    assert find_preference(prefs, make_item("FEIJAO NORMAL")) is prefs["feijao"]
    assert find_preference(prefs, make_item("fiambre", "Presunto")) is prefs["presunto"]
    assert find_preference(prefs, make_item("atum")) is None
    assert preference_quantity(prefs["presunto"]) == Quantity(value=300, unit="g")


def test_normalize_key_drops_accents_and_case():
    assert normalize_key("  Guaraná  Antarctica ") == "guarana antarctica"
