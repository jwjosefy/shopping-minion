import pytest
from pydantic import ValidationError

from shopping_minion.items import CartResult, CartTarget, Item, Quantity


def test_item_defaults():
    item = Item(source_line="atum", name="atum", search_term="atum")
    assert item.constraints == []
    assert item.alternatives == []
    assert item.brand is None
    assert item.quantity is None
    assert item.needs_review is False


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        Item(source_line="a", name="a", search_term="a", color="red")


@pytest.mark.parametrize("value", [0, -1])
def test_quantity_value_must_be_positive(value):
    with pytest.raises(ValidationError):
        Quantity(value=value, unit="kg")


def test_quantity_unit_is_restricted():
    with pytest.raises(ValidationError):
        Quantity(value=1, unit="oz")


@pytest.mark.parametrize("clicks", [0, -2])
def test_cart_target_needs_at_least_one_click(clicks):
    with pytest.raises(ValidationError):
        CartTarget(product_id="1", clicks=clicks)


def test_cart_target_flags_are_restricted():
    with pytest.raises(ValidationError):
        CartTarget(product_id="1", clicks=1, flags=["OTHER"])


def test_cart_result_roundtrip():
    result = CartResult(product_id="1", status="added", quantity_shown="2", message=None)
    assert CartResult.model_validate_json(result.model_dump_json()) == result


def test_an_old_saved_item_without_alternatives_still_loads():
    old = (
        '{"source_line": "atum", "name": "atum", "search_term": "atum", "constraints": [],'
        ' "brand": null, "quantity": null, "needs_review": false}'
    )
    assert Item.model_validate_json(old).alternatives == []


def test_alternatives_round_trip():
    item = Item(source_line="x", name="x", search_term="acém", alternatives=["paleta"])
    assert Item.model_validate_json(item.model_dump_json()).alternatives == ["paleta"]


def test_kg_amount_counts_the_minimum_first():
    from decimal import Decimal

    from shopping_minion.items import Candidate, kg_amount

    def cand(step, minimum):
        return Candidate(
            product_id="1",
            slug="s",
            name="Tempero Granel Kg",
            brand=None,
            price=Decimal("80.00"),
            list_price=None,
            unit_of_sale="kg",
            step_kg=step,
            min_kg=minimum,
            available=True,
        )

    assert kg_amount(cand(0.05, 0.15), 4) == Decimal("0.30")
    assert kg_amount(cand(0.1, None), 3) == Decimal("0.3")
    assert kg_amount(cand(None, None), 3) is None
