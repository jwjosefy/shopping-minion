import pytest

from shopping_minion.items import Item, Quantity
from shopping_minion.quantity import target_quantity, to_clicks


def make_item(quantity: Quantity | None = None) -> Item:
    return Item(source_line="x", name="x", search_term="x", quantity=quantity)


# --- target_quantity: rule A -> B -> C ---


def test_list_quantity_wins_over_preferences():
    item = make_item(Quantity(value=2, unit="kg"))
    pref = {"quantidade": {"valor": 1, "unidade": "kg"}}
    assert target_quantity(item, pref) == (Quantity(value=2, unit="kg"), [])


def test_preferences_used_when_list_has_none():
    pref = {"quantidade": {"valor": 300, "unidade": "g"}}
    assert target_quantity(make_item(), pref) == (Quantity(value=300, unit="g"), [])


def test_default_is_one_unit_flagged():
    assert target_quantity(make_item(), None) == (
        Quantity(value=1, unit="un"),
        ["QUANTITY_ASSUMED"],
    )


def test_pref_entry_without_quantity_falls_through():
    quantity, flags = target_quantity(make_item(), {"marca": "x"})
    assert quantity == Quantity(value=1, unit="un")
    assert flags == ["QUANTITY_ASSUMED"]


# --- to_clicks: product sold by unit ---


@pytest.mark.parametrize("unit", ["un", "pct", "cx", "lata"])
def test_count_units_on_un_product(unit):
    assert to_clicks(Quantity(value=3, unit=unit), "un", None) == (3, [])


def test_dozen_on_un_product():
    assert to_clicks(Quantity(value=2, unit="dz"), "un", None) == (24, [])


def test_fractional_count_on_un_product_is_inexact():
    assert to_clicks(Quantity(value=1.5, unit="un"), "un", None) == (2, ["QUANTITY_INEXACT"])


@pytest.mark.parametrize("unit", ["g", "kg", "ml", "l"])
def test_weight_or_volume_on_un_product_is_one_click_inexact(unit):
    assert to_clicks(Quantity(value=500, unit=unit), "un", None) == (1, ["QUANTITY_INEXACT"])


# --- to_clicks: product sold by kg ---


def test_kg_exact():
    assert to_clicks(Quantity(value=1, unit="kg"), "kg", 0.5) == (2, [])


def test_grams_exact():
    assert to_clicks(Quantity(value=300, unit="g"), "kg", 0.1) == (3, [])


def test_grams_inexact_rounds_up():
    assert to_clicks(Quantity(value=350, unit="g"), "kg", 0.1) == (4, ["QUANTITY_INEXACT"])


def test_no_float_noise_in_exactness():
    # 0.3 / 0.1 is 2.9999999999999996 in floats; it must still count as exact
    assert to_clicks(Quantity(value=0.3, unit="kg"), "kg", 0.1) == (3, [])


def test_small_target_gives_at_least_one_click():
    assert to_clicks(Quantity(value=50, unit="g"), "kg", 0.5) == (1, ["QUANTITY_INEXACT"])


@pytest.mark.parametrize("unit", ["un", "pct", "dz", "ml"])
def test_non_weight_on_kg_product_is_one_click_inexact(unit):
    assert to_clicks(Quantity(value=2, unit=unit), "kg", 0.5) == (1, ["QUANTITY_INEXACT"])


def test_kg_product_without_step_raises():
    with pytest.raises(ValueError):
        to_clicks(Quantity(value=1, unit="kg"), "kg", None)
