"""Regression tests for the committed Andorinha profile, against recorded public responses.

If the site changes its search API, refresh the fixtures with discovery (ADR-0006) and these
tests show what moved.
"""

import json
from pathlib import Path

import pytest

from shopping_minion.catalog.adapter import parse_results, rank
from shopping_minion.catalog.profile import SiteProfile

ROOT = Path(__file__).resolve().parents[1] / "profiles" / "andorinha"


@pytest.fixture(scope="module")
def spec():
    import yaml

    profile = SiteProfile.model_validate(yaml.safe_load((ROOT / "profile.yaml").read_text()))
    assert profile.search is not None
    return profile.search


def candidates(spec, fixture):
    payload = json.loads((ROOT / "fixtures" / fixture).read_text(encoding="utf-8"))["payload"]
    return parse_results(spec, payload)


def test_profile_requires_a_visible_browser_and_a_page_transport():
    import yaml

    profile = SiteProfile.model_validate(yaml.safe_load((ROOT / "profile.yaml").read_text()))
    assert profile.headed and profile.search.transport == "page"


def test_atum_is_sold_by_unit_with_price_and_stock(spec):
    found = candidates(spec, "search-atum.json")
    assert len(found) == 20
    tuna = next(c for c in found if c.name == "Atum Sólido Coqueiro Natural 170g")
    assert tuna.unit_of_sale.kind == "unit" and tuna.brand == "Coqueiro"
    assert str(tuna.price) == "13.98" and tuna.in_stock
    assert tuna.url == (
        "https://andorinhaonline.com.br/busca/Atum%20S%C3%B3lido%20Coqueiro%20Natural%20170g"
    )


def test_chicken_sold_by_kg_becomes_a_weight_step_in_grams(spec):
    found = candidates(spec, "search-file-de-peito-de-frango.json")
    weighed = [c for c in found if c.unit_of_sale.kind == "weight_step"]
    packed_kg = [c for c in found if c.name.endswith("1KG")]
    assert packed_kg and all(c.unit_of_sale.kind == "unit" for c in packed_kg)
    assert all(c.unit_of_sale.step_size_g > 0 for c in weighed)


def test_rolls_are_read_as_packs(spec):
    found = candidates(spec, "search-papel-higienico.json")
    fancy = next(c for c in found if "Fancy Folha Dupla 30m C/16" in c.name)
    assert (fancy.unit_of_sale.kind, fancy.unit_of_sale.pack_size) == ("pack", 16)
    assert any(c.unit_of_sale.kind == "pack" for c in found)


def test_ranking_keeps_the_best_textual_match_first(spec):
    found = candidates(spec, "search-atum.json")
    assert rank("atum sólido coqueiro", found)[0].name.startswith("Atum Sólido Coqueiro")
