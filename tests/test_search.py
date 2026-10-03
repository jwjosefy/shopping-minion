import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

from shopping_minion.items import Item
from shopping_minion.search import (
    apply_synonym,
    candidates_from_response,
    is_search_response,
    load_search_terms,
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
    assert first.image == load("atum")["hits"][0]["image"]
    assert first.image.startswith("https://")


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


@pytest.mark.parametrize("name", ["atum", "file-de-peito-de-frango", "papel-higienico"])
def test_every_fixture_hit_keeps_its_image(name):
    body = load(name)
    candidates = candidates_from_response(body)
    assert [c.image for c in candidates] == [hit["image"] for hit in body["hits"]]


def test_a_hit_without_an_image_gives_none():
    body = load("atum")
    del body["hits"][0]["image"]
    body["hits"][1]["image"] = ""
    candidates = candidates_from_response(body)
    assert candidates[0].image is None
    assert candidates[1].image is None


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


def make_page(handler_calls, per_goto=None):
    """A page double: it hands each registered response listener the responses we choose.

    With `per_goto`, the n-th `goto` hands out `per_goto[n]` (the last one repeats) instead.
    """

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
            self.gotos = []

        def on(self, event, fn):
            assert event == "response"
            self.listeners.append(fn)

        def remove_listener(self, event, fn):
            self.listeners.remove(fn)

        def goto(self, url):
            self.gotos.append(url)
            calls = handler_calls
            if per_goto is not None:
                calls = per_goto[min(len(self.gotos), len(per_goto)) - 1]
            for from_, body in calls:
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
    page2 = {  # other products than the first page's: a repeated product_id is merged away
        "hits": [{**h, "id": 9000 + n} for n, h in enumerate(load("atum")["hits"])],
        "hasNext": False,
    }
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


EMPTY = {"hits": [], "total": 0, "hasNext": False}


def test_an_empty_first_read_is_opened_once_more():
    full = {**load("atum"), "hasNext": False}
    page = make_page(None, per_goto=[[(0, EMPTY)], [(0, full)]])
    result = search(page, Item(source_line="atum", name="atum", search_term="atum"))
    assert len(result) == 12
    assert result.retried is True
    assert len(page.gotos) == 2 and page.gotos[0] == page.gotos[1] == search_url("atum")
    assert page.listeners == []


def test_a_search_with_results_is_not_retried():
    full = {**load("atum"), "hasNext": False}
    page = make_page([(0, full)])
    result = search(page, Item(source_line="atum", name="atum", search_term="atum"))
    assert result.retried is False and len(page.gotos) == 1


def test_still_empty_after_the_retry_gives_an_empty_retried_list():
    page = make_page([(0, EMPTY)])
    result = search(page, Item(source_line="x", name="x", search_term="atum"))
    assert result == [] and result.retried is True
    assert len(page.gotos) == 2  # once more, not a loop


def test_search_all_hands_the_retry_flag_to_progress():
    full = {**load("atum"), "hasNext": False}
    page = make_page(None, per_goto=[[(0, EMPTY)], [(0, full)], [(0, full)]])
    seen = []
    items = [Item(source_line="atum", name="atum", search_term="atum")] * 2
    search_all(page, items, progress=lambda i, n, item, c: seen.append(c.retried))
    assert seen == [True, False]


# --- synonyms and alternatives (LLD-M5 sections 3.2 and 3.3) ------------------------------------


def make_term_page(by_term):
    """A page double whose `goto` answers per searched term: `by_term[term]` is a list of
    bodies, one per visit (the last repeats). Records the terms it was asked for."""

    class FakeResponse:
        def __init__(self, url, body):
            self.url, self._body = url, body

        def json(self):
            return self._body

    class FakeLocator:
        first = property(lambda self: self)

        def is_visible(self):
            return False

        def count(self):
            return 0

    class FakePage:
        def __init__(self):
            self.listeners, self.terms = [], []

        def on(self, event, fn):
            self.listeners.append(fn)

        def remove_listener(self, event, fn):
            self.listeners.remove(fn)

        def goto(self, url):
            term = unquote(url.rsplit("/", 1)[1])
            visits = self.terms.count(term)
            self.terms.append(term)
            bodies = by_term[term]
            body = bodies[min(visits, len(bodies) - 1)]
            for fn in self.listeners:
                fn(FakeResponse(f"https://h/1/2/search?search={quote(term)}&size=12&from=0", body))

        def get_by_text(self, *_):
            return FakeLocator()

        def get_by_role(self, *_, **__):
            return FakeLocator()

        def wait_for_timeout(self, _ms):
            pass

    return FakePage()


def body_of(ids):
    hit = load("atum")["hits"][0]
    return {"hits": [{**hit, "id": i, "slug": f"p-{i}"} for i in ids], "hasNext": False}


def an_item(term, alternatives=()):
    return Item(source_line=term, name=term, search_term=term, alternatives=list(alternatives))


def test_load_search_terms_normalizes_keys(tmp_path):
    path = tmp_path / "terms.yaml"
    path.write_text("Salsinha: salsa\nAÇAFRÃO: açafrão da terra\n", encoding="utf-8")
    assert load_search_terms(path) == {"salsinha": "salsa", "acafrao": "açafrão da terra"}
    assert load_search_terms(tmp_path / "missing.yaml") == {}


def test_the_shipped_file_has_salsinha():
    assert load_search_terms("config/search_terms.yaml")["salsinha"] == "salsa"


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("salsinha", "salsa"),  # hit
        ("  SALSINHA ", "salsa"),  # normalized: case and spaces
        ("Salsínha", "salsa"),  # normalized: accent
        ("salsa", "salsa"),  # miss: as it is
        ("salsinha crespa", "salsinha crespa"),  # only the whole term counts
    ],
)
def test_apply_synonym(term, expected):
    assert apply_synonym(term, {"salsinha": "salsa"}) == expected


def test_the_synonym_is_what_gets_searched_and_the_written_term_is_kept():
    page = make_term_page({"salsa": [body_of([1, 2])]})
    result = search(page, an_item("Salsinha"), {"salsinha": "salsa"})
    assert page.terms == ["salsa"]
    assert result.terms == [("Salsinha", "salsa")]
    assert [c.product_id for c in result] == ["1", "2"]


def test_an_alternative_goes_through_the_synonyms_too():
    page = make_term_page({"acém": [body_of([1])], "salsa": [body_of([2])]})
    result = search(page, an_item("acém", ["salsinha"]), {"salsinha": "salsa"})
    assert result.terms == [("acém", "acém"), ("salsinha", "salsa")]


def test_alternatives_are_searched_and_merged_in_order_without_repeats():
    page = make_term_page(
        {"acém": [body_of([1, 2, 3])], "paleta": [body_of([3, 4])], "peito": [body_of([5, 1])]}
    )
    result = search(page, an_item("acém", ["paleta", "peito"]), {})
    assert page.terms == ["acém", "paleta", "peito"]
    assert [c.product_id for c in result] == ["1", "2", "3", "4", "5"]
    assert result.retried is False


def test_the_merged_list_is_capped_at_15():
    page = make_term_page({"acém": [body_of(range(1, 13))], "paleta": [body_of(range(13, 25))]})
    result = search(page, an_item("acém", ["paleta"]), {})
    assert [c.product_id for c in result] == [str(n) for n in range(1, 16)]


def test_retried_is_true_when_any_of_the_searches_retried():
    page = make_term_page({"acém": [body_of([1])], "paleta": [EMPTY, body_of([2])]})
    result = search(page, an_item("acém", ["paleta"]), {})
    assert result.retried is True
    assert page.terms == ["acém", "paleta", "paleta"]
    assert [c.product_id for c in result] == ["1", "2"]


def test_a_repeated_or_blank_term_is_searched_once():
    page = make_term_page({"acém": [body_of([1])]})
    result = search(page, an_item("acém", ["acém", " "]), {})
    assert page.terms == ["acém"]
    assert len(result) == 1


def test_search_all_is_still_one_progress_call_per_item():
    page = make_term_page({"acém": [body_of([1])], "paleta": [body_of([2])]})
    seen = []
    search_all(
        page,
        [an_item("acém", ["paleta"])],
        progress=lambda i, n, item, c: seen.append((i, n, [x.product_id for x in c])),
    )
    assert seen == [(1, 1, ["1", "2"])]
