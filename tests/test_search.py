import json
from decimal import Decimal
from pathlib import Path

import pytest

from shopping_minion.items import Item
from shopping_minion.search import (
    candidates_from_response,
    is_search_response,
    search,
    search_all,
    search_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "search"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_atum_mapping():
    candidates = candidates_from_response(load("atum"))
    assert len(candidates) == 12
    first = candidates[0]
    assert first.product_id == "6137677"
    assert first.slug == "atum-solido-coqueiro-natural-170g"
    assert first.name == "Atum Sólido Coqueiro Natural 170g"
    assert first.brand == "Coqueiro"
    assert first.price == Decimal("13.98")
    assert first.list_price is None
    assert first.unit_of_sale == "un"
    assert first.step_kg is None
    assert first.available is True


def test_promotion_gives_list_price():
    by_id = {c.product_id: c for c in candidates_from_response(load("atum"))}
    promo = by_id["6131828"]  # Atum Ralado Gomes da Costa: 9.85 -> 8.49
    assert promo.price == Decimal("8.49")
    assert promo.list_price == Decimal("9.85")


def test_kg_product_has_step():
    candidates = candidates_from_response(load("file-de-peito-de-frango"))
    frango = next(c for c in candidates if c.name == "Filé De Peito Frango Resf Kg")
    assert frango.product_id == "6157177"
    assert frango.unit_of_sale == "kg"
    assert frango.step_kg == 0.1
    assert frango.price == Decimal("27.99")
    assert frango.available is True
    other_kg = next(c for c in candidates if c.product_id == "6140902")
    assert other_kg.step_kg == 0.9
    assert all(c.step_kg is None for c in candidates if c.unit_of_sale == "un")


def test_availability_follows_stock():
    body = load("papel-higienico")
    assert all(c.available for c in candidates_from_response(body))
    body["hits"][0]["quantity"]["inStock"] = 0
    assert candidates_from_response(body)[0].available is False


@pytest.mark.parametrize("name", ["atum", "file-de-peito-de-frango", "papel-higienico"])
def test_every_fixture_maps_with_slug_and_id(name):
    for c in candidates_from_response(load(name)):
        assert c.product_id.isdigit()
        assert c.slug
        assert c.price is not None


def test_empty_response_gives_no_candidates():
    assert candidates_from_response({"hits": [], "total": 0, "hasNext": False}) == []


def test_search_url_is_encoded():
    assert search_url("papel higienico").endswith("/busca/papel%20higienico")
    assert search_url("filé/frango").endswith("/busca/fil%C3%A9%2Ffrango")


def test_response_matching_uses_path_and_param_only():
    ok = "https://any.host/1/2/search?search=papel%20higienico&size=12&from=0"
    assert is_search_response(ok, "papel higienico")
    assert not is_search_response(ok, "atum")
    # the page leaves the term percent-encoded inside the param (seen live)
    twice = "https://any.host/1/2/search?search=papel%2520higienico&size=12&from=0"
    assert is_search_response(twice, "papel higienico")
    assert is_search_response("https://any.host/s/search?search=fil%25C3%25A9", "filé")
    assert not is_search_response("https://any.host/1/2/suggest?search=atum", "atum")
    assert not is_search_response("https://any.host/1/2/search?q=atum", "atum")


def make_page(handler_calls):
    """A page double: it hands each registered response listener the responses we choose."""

    class FakeResponse:
        def __init__(self, url, body):
            self.url, self._body = url, body

        def json(self):
            return self._body

    class FakeLocator:
        first = property(lambda self: self)

        def is_visible(self):
            return False

    class FakePage:
        def __init__(self):
            self.listeners = []

        def on(self, event, fn):
            assert event == "response"
            self.listeners.append(fn)

        def remove_listener(self, event, fn):
            self.listeners.remove(fn)

        def goto(self, url):
            for from_, body in handler_calls:
                for fn in self.listeners:
                    fn(FakeResponse(f"https://h/1/2/search?search=atum&size=12&from={from_}", body))

        def get_by_text(self, *_):
            return FakeLocator()

        def get_by_role(self, *_, **__):
            class NoBanner:
                first = property(lambda self: self)

                def count(self):
                    return 0

            return NoBanner()

        def wait_for_timeout(self, _ms):
            pass

    return FakePage()


def test_search_reads_both_pages_and_keeps_15():
    page1 = {**load("atum"), "hasNext": True, "nextFrom": 12}
    page2 = {"hits": load("atum")["hits"], "hasNext": False}
    page = make_page([(0, page1), (12, page2)])
    result = search(page, Item(source_line="atum", name="atum", search_term="atum"))
    assert len(result) == 15
    assert page.listeners == []  # listener removed afterwards


def test_search_all_reports_progress():
    body = {**load("atum"), "hasNext": False}
    page = make_page([(0, body)])
    seen = []
    items = [Item(source_line="atum", name="atum", search_term="atum")] * 2
    results = search_all(page, items, progress=lambda i, n, item, c: seen.append((i, n, len(c))))
    assert [len(r) for r in results] == [12, 12]
    assert seen == [(1, 2, 12), (2, 2, 12)]


@pytest.mark.live
def test_live_search_atum():
    from shopping_minion.browser import open_browser

    with open_browser() as (_browser, context):
        page = context.new_page()
        candidates = search(page, Item(source_line="atum", name="atum", search_term="atum"))
    print(f"atum: {len(candidates)} candidates")
    assert len(candidates) >= 12
    assert all(c.product_id and c.slug and c.name for c in candidates)
