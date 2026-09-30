from decimal import Decimal

import pytest

from shopping_minion.catalog.mapping import DomSource, ResponseSource
from shopping_minion.catalog.reading import (
    ReadingError,
    _price,
    candidates_from_html,
    candidates_from_response,
    get_path,
)
from shopping_minion.contracts import Candidate, UnitOfSale

UNIT_RULE = {
    "weight_when": {"path": "sold_by", "equals": ["KG"]},
    "step_g": {"path": "step", "scale": 1000},
    "pack_size": {"path": "name", "regex": r"(\d+)\s*rolos"},
}
STOCK = {"path": "stock", "matches": "^(in_stock|available)$"}

RESPONSE = ResponseSource.model_validate(
    {
        "url_matches": "/search",
        "items": "data.products",
        "fields": {
            "id": "id",
            "name": "title",
            "url": "https://loja.example/p/{slug}",
            "brand": "brand.name",
            "size": "size",
            "price": "prices[0].value",
            "in_stock": STOCK,
        },
        "unit_of_sale": {**UNIT_RULE, "pack_size": {"path": "title", "regex": r"(\d+)\s*rolos"}},
    }
)

DOM = DomSource.model_validate(
    {
        "item": "li.card",
        "extract": {
            "id": {"selector": "a.link", "attr": "data-id"},
            "slug": {"selector": "a.link", "attr": "data-slug"},
            "title": {"selector": "h2"},
            "brand": {"selector": ".brand"},
            "size": {"selector": ".size"},
            "price": {"selector": ".price"},
            "stock": {"selector": ".stock"},
            "sold_by": {"selector": ".sold-by"},
            "step": {"selector": ".step"},
        },
        "fields": {
            "id": "id",
            "name": "title",
            "url": "https://loja.example/p/{slug}",
            "brand": "brand",
            "size": "size",
            "price": "price",
            "in_stock": STOCK,
        },
        "unit_of_sale": {**UNIT_RULE, "pack_size": {"path": "title", "regex": r"(\d+)\s*rolos"}},
    }
)

PAYLOAD = {
    "data": {
        "products": [
            {
                "id": 101,
                "title": "Feijao Carioca Tio Ze 1kg",
                "slug": "feijao-carioca",
                "brand": {"name": "Tio Ze"},
                "size": "1 kg",
                "prices": [{"value": 8.9}],
                "stock": "in_stock",
                "sold_by": "UN",
            },
            {
                "id": 102,
                "title": "Papel Higienico Neve C/16 Rolos",
                "slug": "papel-neve",
                "brand": {"name": "Neve"},
                "prices": [{"value": "R$ 1.234,56"}],
                "stock": "sold_out",
                "sold_by": "UN",
            },
            {
                "id": 103,
                "title": "File de Frango",
                "slug": "file de frango/kg",
                "brand": {"name": "Granja"},
                "prices": [{"value": 22.5}],
                "stock": "available",
                "sold_by": "KG",
                "step": 0.5,
            },
        ]
    }
}

HTML = """
<ul>
  <li class="card">
    <a class="link" data-id="101" data-slug="feijao-carioca">x</a>
    <h2> Feijao Carioca Tio Ze 1kg </h2><span class="brand">Tio Ze</span>
    <span class="size">1 kg</span><span class="price">R$ 8,90</span>
    <span class="stock">in_stock</span><span class="sold-by">UN</span>
  </li>
  <li class="card">
    <a class="link" data-id="102" data-slug="papel-neve">x</a>
    <h2>Papel Higienico Neve C/16 Rolos</h2><span class="brand">Neve</span>
    <span class="price">R$ 1.234,56</span>
    <span class="stock">sold_out</span><span class="sold-by">UN</span>
  </li>
  <li class="card">
    <a class="link" data-id="103" data-slug="file de frango/kg">x</a>
    <h2>File de Frango</h2><span class="brand">Granja</span>
    <span class="price">22,50</span><span class="stock">available</span>
    <span class="sold-by">KG</span><span class="step">0,5</span>
  </li>
  <li class="card"><h2>No id here</h2></li>
</ul>
"""


def with_items(*items):
    return {"data": {"products": list(items)}}


def test_response_maps_unit_pack_and_weight_step():
    unit, pack, weight = candidates_from_response(RESPONSE, PAYLOAD)
    assert unit.unit_of_sale == UnitOfSale(kind="unit")
    assert (unit.id, unit.brand, unit.size, unit.price) == ("101", "Tio Ze", "1 kg", Decimal("8.9"))
    assert pack.unit_of_sale == UnitOfSale(kind="pack", pack_size=16)
    assert weight.unit_of_sale == UnitOfSale(kind="weight_step", step_size_g=500)


def test_html_maps_unit_pack_and_weight_step():
    unit, pack, weight = candidates_from_html(DOM, HTML)
    assert unit.unit_of_sale.kind == "unit" and unit.name == "Feijao Carioca Tio Ze 1kg"
    assert pack.unit_of_sale.pack_size == 16 and pack.size is None
    assert weight.unit_of_sale.step_size_g == 500


def test_same_product_in_json_and_html_gives_equal_candidates():
    from_json = candidates_from_response(RESPONSE, PAYLOAD)
    from_html = candidates_from_html(DOM, HTML)
    assert from_html == from_json
    assert all(isinstance(c, Candidate) for c in from_html)


def test_html_extracts_attributes_and_skips_missing_elements():
    html = '<div class="c"><a class="l" href="/p/1">Name</a></div>'
    source = DomSource.model_validate(
        {
            "item": "div.c",
            "extract": {
                "id": {"selector": "a.l", "attr": "href"},
                "name": {"selector": "a.l"},
                "brand": {"selector": ".nope"},
                "size": {"selector": "a.l", "attr": "data-none"},
            },
            "fields": {"id": "id", "name": "name", "url": "/x/{id}", "brand": "brand"},
        }
    )
    [c] = candidates_from_html(source, html)
    assert (c.id, c.name, c.brand, c.size) == ("/p/1", "Name", None, None)
    assert c.url == "/x/%2Fp%2F1"


def test_url_values_are_quoted():
    weight = candidates_from_response(RESPONSE, PAYLOAD)[2]
    assert weight.url == "https://loja.example/p/file%20de%20frango%2Fkg"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (12.9, "12.9"),
        (5, "5"),
        ("R$ 1.234,56", "1234.56"),
        ("9,99", "9.99"),
        ("", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_price_parsing(raw, expected):
    assert _price(raw) == (Decimal(expected) if expected else None)


def test_price_missing_gives_none():
    [c] = candidates_from_response(RESPONSE, with_items({"id": 1, "title": "A", "prices": []}))
    assert c.price is None


def test_stock_condition_and_default():
    pack = candidates_from_response(RESPONSE, PAYLOAD)[1]
    assert not pack.in_stock
    no_condition = RESPONSE.model_copy(
        update={"fields": RESPONSE.fields.model_copy(update={"in_stock": None})}
    )
    assert candidates_from_response(no_condition, PAYLOAD)[1].in_stock


def test_condition_without_equals_or_matches_is_truthiness():
    data = RESPONSE.model_dump()
    data["fields"]["in_stock"] = {"path": "qty"}
    source = ResponseSource.model_validate(data)
    payload = with_items(
        {"id": 1, "title": "A", "qty": 3},
        {"id": 2, "title": "B", "qty": 0},
        {"id": 3, "title": "C"},
    )
    assert [c.in_stock for c in candidates_from_response(source, payload)] == [True, False, False]


def test_items_without_id_or_name_are_skipped():
    payload = with_items({"title": "no id"}, {"id": 2}, {"id": 3, "title": "ok"})
    assert [c.id for c in candidates_from_response(RESPONSE, payload)] == ["3"]
    assert len(candidates_from_html(DOM, HTML)) == 3  # the card without id is skipped


def test_pack_of_one_is_a_unit():
    [c] = candidates_from_response(RESPONSE, with_items({"id": 1, "title": "Papel 1 rolos"}))
    assert c.unit_of_sale.kind == "unit"


def test_pack_size_from_name_lowercase():
    [c] = candidates_from_response(RESPONSE, with_items({"id": 1, "title": "Papel 12 rolos"}))
    assert c.unit_of_sale == UnitOfSale(kind="pack", pack_size=12)


def test_items_path_that_is_not_a_list_is_a_reading_error():
    with pytest.raises(ReadingError, match=r"list at 'data\.products'"):
        candidates_from_response(RESPONSE, {"data": {"products": {"a": 1}}})
    with pytest.raises(ReadingError, match="list"):
        candidates_from_response(RESPONSE, {"other": []})


@pytest.mark.parametrize("step", [None, 0, "abc"])
def test_weight_product_without_step_is_a_reading_error(step):
    payload = with_items({"id": 1, "title": "Carne", "sold_by": "KG", "step": step})
    with pytest.raises(ReadingError, match="step_g"):
        candidates_from_response(RESPONSE, payload)


def test_reading_error_is_a_value_error():
    assert issubclass(ReadingError, ValueError)


def test_get_path_reads_nested_dicts_and_lists():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert get_path(data, "a.b[1].c") == 2
    assert get_path(data, "a.b[5].c") is get_path(data, "a.x")
