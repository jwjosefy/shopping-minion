import pytest
from pydantic import ValidationError

from shopping_minion.contracts import (
    ConfirmedItem,
    Decision,
    DecisionStatus,
    ReportItem,
    RunReport,
    UnitOfSale,
)

ITEM = ConfirmedItem(name="atum")


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "unit"},
        {"kind": "pack", "pack_size": 12},
        {"kind": "weight_step", "step_size_g": 500},
    ],
)
def test_unit_of_sale_valid(fields):
    assert UnitOfSale(**fields).kind == fields["kind"]


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "weight_step"},
        {"kind": "pack"},
        {"kind": "unit", "pack_size": 12},
        {"kind": "pack", "pack_size": 12, "step_size_g": 100},
    ],
)
def test_unit_of_sale_rejects_mismatched_fields(fields):
    with pytest.raises(ValidationError):
        UnitOfSale(**fields)


def test_confidence_is_min_of_both_probabilities():
    d = Decision(item=ITEM, p_product=0.9, p_quantity=0.6, status=DecisionStatus.ADDED)
    assert d.confidence == 0.6


def test_confidence_is_product_only_when_quantity_not_asked():
    d = Decision(item=ITEM, p_product=0.8, status=DecisionStatus.ADDED)
    assert d.confidence == 0.8


def test_confidence_absent_without_product_decision():
    assert Decision(item=ITEM, status=DecisionStatus.NOT_FOUND).confidence is None


def test_probability_bounds():
    with pytest.raises(ValidationError):
        Decision(item=ITEM, p_product=1.2, status=DecisionStatus.ADDED)


def test_report_counts_every_status():
    report = RunReport(
        run_id="r1",
        items=[
            ReportItem(decision=Decision(item=ITEM, status=DecisionStatus.ADDED)),
            ReportItem(decision=Decision(item=ITEM, status=DecisionStatus.NOT_SURE)),
            ReportItem(decision=Decision(item=ITEM, status=DecisionStatus.ADDED)),
        ],
    )
    assert report.counts[DecisionStatus.ADDED] == 2
    assert report.counts[DecisionStatus.NOT_SURE] == 1
    assert report.counts[DecisionStatus.FAILED] == 0


def test_contracts_reject_unknown_fields():
    with pytest.raises(ValidationError):
        ConfirmedItem(name="atum", brand="Gomes da Costa")


def test_computed_fields_survive_a_json_round_trip():
    decision = Decision(item=ITEM, p_product=0.9, p_quantity=0.6, status=DecisionStatus.ADDED)
    report = RunReport(run_id="r", items=[ReportItem(decision=decision)])
    text = report.model_dump_json()
    assert '"confidence":0.6' in text and '"counts"' in text
    assert RunReport.model_validate_json(text) == report
