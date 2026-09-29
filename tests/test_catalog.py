from decimal import Decimal

import pytest
from pydantic import ValidationError

from shopping_minion.catalog.adapter import (
    ProfileError,
    _price,
    get_path,
    parse_results,
    rank,
)
from shopping_minion.catalog.profile import HttpSearch

SPEC = HttpSearch.model_validate(
    {
        "method": "POST",
        "url": "https://api.store.com.br/graphql",
        "body": {"query": "search", "variables": {"term": "{query}", "first": "{limit}"}},
        "results_path": "data.search.items",
        "fields": {
            "id": "id",
            "name": "title",
            "url": "https://store.com.br/p/{slug}",
            "brand": "brand.name",
            "price": "prices[0].value",
            "in_stock": {"path": "stock", "matches": "^(in_stock|available)$"},
        },
        "unit_of_sale": {
            "weight_when": {"path": "saleUnit", "equals": ["KG"]},
            "step_g": {"path": "step", "scale": 1000},
            "pack_size": {"path": "name", "regex": r"(\d+)\s*rolos"},
        },
    }
)


def payload(*items):
    return {"data": {"search": {"items": list(items)}}}


def item(**overrides):
    base = {
        "id": 1,
        "title": "Atum Sólido Gomes da Costa 170g",
        "slug": "atum-solido",
        "brand": {"name": "Gomes da Costa"},
        "prices": [{"value": 12.9}],
        "stock": "in_stock",
        "saleUnit": "UN",
    }
    return base | overrides


def test_get_path_reads_nested_dicts_and_lists():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert get_path(data, "a.b[1].c") == 2
    assert get_path(data, "a.x") is not None and get_path(data, "a.b[5].c") is get_path(data, "a.x")


def test_parse_results_maps_fields():
    [c] = parse_results(SPEC, payload(item()))
    assert (c.id, c.brand, c.price) == ("1", "Gomes da Costa", Decimal("12.9"))
    assert c.url == "https://store.com.br/p/atum-solido"
    assert c.unit_of_sale.kind == "unit" and c.in_stock


def test_unit_of_sale_weight_step_in_grams():
    [c] = parse_results(SPEC, payload(item(title="Filé de peito de frango", saleUnit="KG", step=0.5)))
    assert c.unit_of_sale.kind == "weight_step" and c.unit_of_sale.step_size_g == 500


def test_unit_of_sale_pack_from_name():
    [c] = parse_results(SPEC, payload(item(title="Papel Higiênico Neve Folha Dupla 12 Rolos")))
    assert c.unit_of_sale.kind == "pack" and c.unit_of_sale.pack_size == 12


def test_weight_without_step_is_a_profile_error():
    with pytest.raises(ProfileError, match="step_g"):
        parse_results(SPEC, payload(item(saleUnit="KG")))


def test_wrong_results_path_is_a_profile_error():
    with pytest.raises(ProfileError, match="results_path"):
        parse_results(SPEC, {"data": {}})


def test_items_without_id_or_name_are_skipped():
    assert parse_results(SPEC, payload(item(id=None), item(id=2))) != []
    assert len(parse_results(SPEC, payload(item(id=None), item(id=2)))) == 1


def test_out_of_stock_condition():
    [c] = parse_results(SPEC, payload(item(stock="sold_out")))
    assert not c.in_stock


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(12.9, "12.9"), ("R$ 1.234,56", "1234.56"), ("9,99", "9.99"), ("", None), ("n/a", None)],
)
def test_price_parsing(raw, expected):
    assert _price(raw) == (Decimal(expected) if expected else None)


def test_rank_prefers_in_stock_then_query_overlap():
    candidates = parse_results(
        SPEC,
        payload(
            item(id=1, title="Molho de tomate"),
            item(id=2, title="Atum ralado", stock="sold_out"),
            item(id=3, title="Atum sólido"),
        ),
    )
    assert [c.id for c in rank("atum sólido", candidates)] == ["3", "1", "2"]


def test_rank_caps_at_twenty():
    many = parse_results(SPEC, payload(*[item(id=i) for i in range(30)]))
    assert len(rank("atum", many)) == 20


@pytest.mark.parametrize("header", ["Cookie", "authorization", "X-Api-Key"])
def test_profiles_refuse_credential_headers(header):
    with pytest.raises(ValidationError, match="credential"):
        HttpSearch.model_validate({**SPEC.model_dump(), "headers": {header: "x"}})


def test_profiles_refuse_bearer_values():
    with pytest.raises(ValidationError, match="credential"):
        HttpSearch.model_validate({**SPEC.model_dump(), "headers": {"x-token": "Bearer abc"}})
