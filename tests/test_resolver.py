from decimal import Decimal

import pytest

from shopping_minion.contracts import (
    Alternative,
    Candidate,
    ConfirmedItem,
    DecisionFlag,
    DecisionStatus,
    UnitOfSale,
)
from shopping_minion.preferences import ItemPreference, Preferences
from shopping_minion.resolver import ProductChoice, resolve, target_quantity


def cand(id, name, **unit):
    return Candidate(
        id=id,
        name=name,
        unit_of_sale=UnitOfSale(**(unit or {"kind": "unit"})),
        price=Decimal(10),
        url=f"https://s/{id}",
    )


class Backend:
    name = "stub"

    def __init__(self, choice):
        self.choice, self.calls = choice, []

    def choose(self, item, candidates, preference):
        self.calls.append((item, candidates, preference))
        return self.choice


PREFS = Preferences(
    {
        "beans": ItemPreference(aliases=["feijão"], default_quantity={"value": 1, "unit": "kg"}),
        "toilet_paper": ItemPreference(aliases=["papel higiênico"], default_quantity=2),
    }
)


def test_written_quantity_wins_and_is_not_assumed():
    target, assumed = target_quantity(ConfirmedItem(name="presunto", quantity=600, unit="g"), None)
    assert (target.amount, target.unit, assumed) == (600, "g", False)


def test_default_quantity_from_preferences():
    _, pref = PREFS.find("feijão")
    target, assumed = target_quantity(ConfirmedItem(name="feijão"), pref)
    assert (target.amount, target.unit, assumed) == (1, "kg", False)
    _, pref = PREFS.find("papel higiênico")
    assert target_quantity(ConfirmedItem(name="papel higiênico"), pref)[0].amount == 2


def test_nothing_written_nothing_preferred_is_one_unit_assumed():
    target, assumed = target_quantity(ConfirmedItem(name="atum"), None)
    assert (target.amount, target.unit, assumed) == (1, "unit", True)


def test_unknown_unit_is_counted_and_flagged():
    _, assumed = target_quantity(ConfirmedItem(name="x", quantity=2, unit="latas"), None)
    assert assumed


def test_no_candidates_is_not_found_without_calling_the_model():
    backend = Backend(ProductChoice("1", 0.9))
    result = resolve(ConfirmedItem(name="atum"), [], PREFS, backend)
    assert result.decision.status == DecisionStatus.NOT_FOUND and not backend.calls


def test_needs_clarification_skips_the_model():
    backend = Backend(ProductChoice("1", 0.9))
    result = resolve(
        ConfirmedItem(name="lanches", needs_clarification=True), [cand("1", "x")], PREFS, backend
    )
    assert result.decision.status == DecisionStatus.NOT_SURE
    assert result.sale_quantity is None and not backend.calls


def test_confident_pick_is_added_with_converted_quantity():
    item = ConfirmedItem(name="frango", quantity=1, unit="kg")
    chosen = cand("7", "Filé de peito", kind="weight_step", step_size_g=500)
    result = resolve(item, [cand("6", "Coxa"), chosen], PREFS, Backend(ProductChoice("7", 0.92)))
    assert result.decision.status == DecisionStatus.ADDED
    assert result.sale_quantity.steps_or_units == 2 and result.sale_quantity.exact
    assert DecisionFlag.QUANTITY_INEXACT not in result.decision.flags


def test_middle_confidence_is_added_and_flagged_as_low_confidence():
    result = resolve(
        ConfirmedItem(name="atum"),
        [cand("1", "Atum")],
        PREFS,
        Backend(ProductChoice("1", 0.6, [Alternative(candidate_id="2", p=0.3)], "close call")),
    )
    d = result.decision
    assert d.status == DecisionStatus.ADDED_LOW_CONFIDENCE and result.sale_quantity is not None
    assert DecisionFlag.QUANTITY_ASSUMED in d.flags and d.rationale == "close call"


def test_low_confidence_is_skipped_but_reported():
    result = resolve(
        ConfirmedItem(name="atum"), [cand("1", "Atum")], PREFS, Backend(ProductChoice("1", 0.3))
    )
    assert result.decision.status == DecisionStatus.NOT_SURE and result.sale_quantity is None
    assert result.decision.candidate_id == "1"  # what it would have picked stays visible


@pytest.mark.parametrize("choice", [ProductChoice(None, 0.2), ProductChoice("ghost", 0.9)])
def test_none_of_these_or_unknown_id_is_not_sure(choice):
    result = resolve(ConfirmedItem(name="atum"), [cand("1", "Atum")], PREFS, Backend(choice))
    assert result.decision.status == DecisionStatus.NOT_SURE
    assert result.decision.candidate_id is None


def test_inexact_conversion_is_flagged():
    item = ConfirmedItem(name="x", quantity=1, unit="kg")
    result = resolve(
        item,
        [cand("1", "x", kind="weight_step", step_size_g=700)],
        PREFS,
        Backend(ProductChoice("1", 0.95)),
    )
    assert DecisionFlag.QUANTITY_INEXACT in result.decision.flags


def test_preference_hints_reach_the_backend():
    backend = Backend(ProductChoice("1", 0.9))
    resolve(ConfirmedItem(name="feijão"), [cand("1", "Feijão")], PREFS, backend)
    assert backend.calls[0][2]["default_quantity"]["unit"] == "kg"
