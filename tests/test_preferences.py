from pathlib import Path

from shopping_minion.preferences import Preferences

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "preferences.example.yaml"


def test_example_file_matches_aliases_ignoring_accents_and_case():
    prefs = Preferences.load(EXAMPLE)
    assert prefs.find("Filtro Melita")[0] == "coffee_filter"
    assert prefs.find("papel higienico")[0] == "toilet_paper"
    assert prefs.find("feijao normal")[0] == "beans"
    assert prefs.find("atum") is None


def test_hints_pass_unknown_keys_and_drop_empty_ones():
    hints = Preferences.load(EXAMPLE).find("presunto")[1].hints()
    assert hints["sliced"] is True and "aliases" not in hints and "exclude" not in hints


def test_missing_file_means_no_preferences(tmp_path):
    assert Preferences.load(tmp_path / "nope.yaml").find("atum") is None
