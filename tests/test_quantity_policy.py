import pytest

from shopping_minion.contracts import Candidate, DecisionStatus, TargetQuantity, UnitOfSale
from shopping_minion.executor_quantity import convert
from shopping_minion.policy import Thresholds, classify


def candidate(**unit):
    return Candidate(id="p1", name="x", unit_of_sale=UnitOfSale(**unit), url="https://s/x")


def target(amount, unit):
    return TargetQuantity(id="t", label="t", amount=amount, unit=unit)


def test_weight_step_exact():
    q = convert(target(600, "g"), candidate(kind="weight_step", step_size_g=200))
    assert (q.steps_or_units, q.effective_amount, q.effective_unit, q.exact) == (3, 600, "g", True)


def test_weight_step_kg_and_rounding_is_inexact():
    q = convert(target(1, "kg"), candidate(kind="weight_step", step_size_g=700))
    assert (q.steps_or_units, q.effective_amount, q.exact) == (1, 700, False)


@pytest.mark.parametrize(
    ("grams", "steps"), [(100, 1), (250, 1), (300, 1), (749, 1), (750, 2), (1250, 3)]
)
def test_rounding_is_half_up_and_never_below_one(grams, steps):
    q = convert(target(grams, "g"), candidate(kind="weight_step", step_size_g=500))
    assert q.steps_or_units == steps


def test_unit_and_pack_counts():
    assert convert(target(2, "unit"), candidate(kind="unit")).steps_or_units == 2
    q = convert(target(1, "unit"), candidate(kind="pack", pack_size=12))
    assert (q.steps_or_units, q.effective_unit, q.exact) == (1, "pack", True)


def test_weight_asked_for_unit_product_is_flagged_inexact():
    q = convert(target(500, "g"), candidate(kind="unit"))
    assert (q.steps_or_units, q.exact) == (1, False)


def test_units_asked_for_weight_product_is_one_step_inexact():
    q = convert(target(2, "unit"), candidate(kind="weight_step", step_size_g=500))
    assert (q.steps_or_units, q.effective_amount, q.exact) == (1, 500, False)


def test_quantity_is_bounded():
    assert convert(target(5000, "unit"), candidate(kind="unit")).steps_or_units == 99


@pytest.mark.parametrize(
    ("confidence", "clarify", "status"),
    [
        (0.95, False, DecisionStatus.ADDED),
        (0.8, False, DecisionStatus.ADDED),
        (0.79, False, DecisionStatus.ADDED_LOW_CONFIDENCE),
        (0.5, False, DecisionStatus.ADDED_LOW_CONFIDENCE),
        (0.49, False, DecisionStatus.NOT_SURE),
        (0.99, True, DecisionStatus.NOT_SURE),
        (None, False, DecisionStatus.NOT_SURE),
    ],
)
def test_classify(confidence, clarify, status):
    assert classify(confidence, needs_clarification=clarify) == status


def test_thresholds_validate_order():
    with pytest.raises(ValueError):
        Thresholds(high=0.4, skip=0.6)
