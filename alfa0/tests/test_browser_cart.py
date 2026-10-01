"""Pure parts of the cart executor and its construction. No browser, no page (LLD 2.1, 8: the
page-driving code is first run against the real store in a supervised session)."""

from unittest.mock import MagicMock

import pytest

from shopping_minion.catalog.profile import SiteProfile
from shopping_minion.catalog.reading import ReadLine
from shopping_minion.contracts import Candidate, SaleQuantity, UnitOfSale
from shopping_minion.executor.browser_cart import (
    BrowserCartExecutor,
    CartConflictError,
    find_line,
    line_matches,
    normalize_name,
    quantity_matches,
    stepper_clicks,
)

FIELDS = {"id": "id", "name": "name", "url": "/p/{id}"}
CART = {
    "product_card": ".card",
    "add": [{"click": ".add"}],
    "quantity": {"kind": "stepper", "click": ".plus"},
    "read": {
        "steps": [{"open": "{base_url}cart"}],
        "from_dom": {
            "item": ".line",
            "extract": {"id": {"selector": ".line", "attr": "data-id"}, "name": {"selector": "a"}},
            "fields": FIELDS,
        },
    },
    "remove": [{"click": ".rm"}],
    "checkout_markers": ["text=Checkout"],
}
SEARCH = {
    "steps": [{"open": "{base_url}busca/{query}"}],
    "results": {
        "wait_for": ".card",
        "from_dom": {
            "item": ".card",
            "extract": {"id": {"selector": ".card", "attr": "data-id"}, "name": {"selector": "a"}},
            "fields": FIELDS,
        },
    },
}


def profile(**sections) -> SiteProfile:
    data = {"store": "examplestore", "version": 1, "base_url": "https://store.example/"}
    data.update(sections)
    return SiteProfile.model_validate(data)


def cand(id_: str, name: str) -> Candidate:
    return Candidate(id=id_, name=name, unit_of_sale=UnitOfSale(kind="unit"), url="/p/x")


def sale(steps: int, amount: float, unit: str) -> SaleQuantity:
    return SaleQuantity(
        candidate_id="1",
        steps_or_units=steps,
        effective_amount=amount,
        effective_unit=unit,
        exact=True,
    )


# --- construction -----------------------------------------------------------------------------


def test_needs_a_cart_section():
    with pytest.raises(ValueError, match="'cart'"):
        BrowserCartExecutor(profile(search=SEARCH), MagicMock())


def test_needs_a_search_section():
    with pytest.raises(ValueError, match="'search'"):
        BrowserCartExecutor(profile(cart=CART), MagicMock())


def test_builds_without_touching_the_context():
    context = MagicMock()
    BrowserCartExecutor(profile(cart=CART, search=SEARCH), context)
    assert context.mock_calls == []


def test_conflict_error_is_a_runtime_error():
    assert issubclass(CartConflictError, RuntimeError)


# --- matching a cart line to a candidate -------------------------------------------------------


def test_names_are_compared_ignoring_case_accents_and_spacing():
    assert normalize_name("  CAFÉ   Torrado ") == "cafe torrado"
    assert line_matches(cand("a", "Café Torrado"), cand("b", "cafe  torrado"))


def test_equal_ids_match_even_with_different_names():
    assert line_matches(cand("42", "Leite Integral 1L"), cand("42", "Leite"))


def test_different_ids_and_different_names_do_not_match():
    assert not line_matches(cand("1", "Leite"), cand("2", "Leite Desnatado"))


def test_different_ids_fall_back_to_the_name():
    assert line_matches(cand("cart-9", "Leite"), cand("prod-1", "leite"))


def test_find_line_returns_the_first_match_or_none():
    lines = [ReadLine(cand("1", "Arroz"), 1.0), ReadLine(cand("2", "Leite"), 3.0)]
    assert find_line(lines, cand("x", "LEITE")) is lines[1]
    assert find_line(lines, cand("x", "Feijão")) is None
    assert find_line([], cand("x", "Leite")) is None


# --- comparing quantities ----------------------------------------------------------------------


def test_count_compares_with_steps_or_units():
    assert quantity_matches(3, sale(3, 600, "g"), "count")
    assert not quantity_matches(2, sale(3, 600, "g"), "count")


def test_grams_compare_with_effective_amount():
    assert quantity_matches(600, sale(3, 600, "g"), "g")
    assert not quantity_matches(300, sale(3, 600, "g"), "g")


def test_kilograms_times_1000_compare_with_effective_amount():
    assert quantity_matches(0.6, sale(3, 600, "g"), "kg")
    assert quantity_matches(1.1, sale(11, 1100, "g"), "kg")  # 1.1 * 1000 is not exactly 1100
    assert not quantity_matches(0.5, sale(3, 600, "g"), "kg")


def test_uncomparable_units_fall_back_to_count():
    assert quantity_matches(2, sale(2, 2, "unit"), "g")
    assert quantity_matches(2, sale(2, 2, "pack"), "kg")
    assert not quantity_matches(600, sale(2, 2, "unit"), "g")


# --- stepper clicks ----------------------------------------------------------------------------


@pytest.mark.parametrize(("target", "clicks"), [(1, 0), (2, 1), (5, 4)])
def test_stepper_clicks_is_target_minus_one(target, clicks):
    assert stepper_clicks(target) == clicks
