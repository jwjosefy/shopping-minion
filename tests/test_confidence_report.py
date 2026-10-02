"""evals/confidence_report.py on a made-up run: no model call."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

from shopping_minion.items import Candidate, Decision, Item
from shopping_minion.storage import Storage

sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))
import confidence_report  # noqa: E402


def cand(product_id: str) -> Candidate:
    return Candidate(
        product_id=product_id,
        slug=f"p-{product_id}",
        name=f"Produto {product_id}",
        brand=None,
        price=Decimal("1.00"),
        list_price=None,
        unit_of_sale="un",
        step_kg=None,
        available=True,
    )


def decision(name: str, status: str, choice: str | None, confidence: float | None) -> Decision:
    item = Item(source_line=name, name=name, search_term=name)
    candidates = [cand("1"), cand("2")] if status != "no_candidates" else []
    return Decision(
        item=item,
        candidates=candidates,
        choice=choice,
        confidence=confidence,
        status="skipped" if status == "no_candidates" else status,
    )


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "t.sqlite")
    run = db.new_run(None)
    db.save_decisions(
        run,
        [
            decision("leite", "accepted", "1", 0.95),  # hit
            decision("arroz", "user_chosen", "2", None),  # pick row: Jev said 1 -> miss
            decision("sal", "user_chosen", "1", None),  # no pick row: lost
            decision("cafe", "accepted", "2", 0.91),  # removed in cart review -> miss
            decision("pimenta", "no_candidates", None, None),  # never reached Jev
        ],
    )
    db.log(run, "decide", {"history": "options"})
    db.log(run, "pick", {"index": 1, "jev_choice": "1", "jev_confidence": 0.4, "chosen": "2"})
    db.log(run, "cart_edit", {"line_id": "2", "quantity": None, "remove": True})
    yield db, run
    db.close()


def test_catalog_run(storage):
    db, run = storage
    answers, lost = confidence_report.catalog_run(db, run)
    assert lost == 1
    assert [(a.item, a.confidence, a.outcome, a.variant) for a in answers] == [
        ("leite", 0.95, "hit", "options"),
        ("arroz", 0.4, "miss", "options"),
        ("cafe", 0.91, "miss", "options"),
    ]


def test_percentile_is_linear_between_ranks():
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert confidence_report.percentile(values, 50) == 0.3
    assert confidence_report.percentile(values, 75) == pytest.approx(0.4)
    assert confidence_report.percentile(values, 90) == pytest.approx(0.46)
    assert confidence_report.percentile([], 50) is None
