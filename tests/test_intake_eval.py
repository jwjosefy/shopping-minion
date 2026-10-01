import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))

from intake_eval import compare, norm  # noqa: E402

from shopping_minion.items import Item  # noqa: E402

FIXTURE = yaml.safe_load(
    """
lines:
  - raw: "Farofa / sal"
    items: [{ name: farofa }, { name: sal }]
  - raw: "Feijão normal / preto não"
    items: [{ name: feijão, constraints: [normal, "not preto"] }]
  - raw: "Requeijão"
    items: [{ name: requeijão }]
  - raw: "Margarina Vigor mix"
    items: [{ name: margarina, brand: Vigor, variant: mix }]
  - raw: "Requeijão"
    items: [{ name: requeijão }]
  - raw: "Presunto 600 g"
    items: [{ name: presunto, quantity: { value: 600, unit: g } }]
  - raw: "Lanches das crianças"
    items: [{ name: lanches das crianças, needs_clarification: true }]
"""
)


def item(line, name, **kw):
    return Item(source_line=line, name=name, search_term=name, **kw)


def perfect() -> list[Item]:
    return [
        item("Farofa / sal", "Farofa"),
        item("Farofa / sal", "sal"),
        item("Feijão normal / preto não", "feijao", constraints=["Normal", "não preto"]),
        item("Requeijao", "requeijão"),
        item("Margarina Vigor mix", "margarina", brand="Vigor", constraints=["mix"]),
        item("Requeijao", "requeijão"),
        item("Presunto 600g", "presunto", quantity={"value": 600, "unit": "g"}),
        item("Lanches das criancas", "lanches das crianças", needs_review=True),
    ]


def test_norm_ignores_accents_and_case():
    assert norm("  Feijão  NORMAL ") == "feijao normal"


def test_perfect_result_scores_everything():
    report = compare(FIXTURE, perfect())
    assert report.found == report.expected_items == 8
    assert not report.missed and not report.extras and not report.problems
    assert all(line.split_ok for line in report.lines)
    assert (report.constraints_ok, report.brand_ok, report.quantity_ok) == (8, 8, 8)
    assert (report.review_flagged, report.review_expected) == (1, 1)


def test_merged_line_is_a_wrong_split_and_a_missed_item():
    items = perfect()
    items[0:2] = [item("Farofa / sal", "farofa")]
    report = compare(FIXTURE, items)
    assert [line.split_ok for line in report.lines][:2] == [False, True]
    assert report.missed == ["sal"]


def test_missing_line_extras_and_wrong_details():
    items = perfect()
    items[4] = item("Margarina Vigor mix", "margarina", brand="Qualy")
    items[6] = item("Presunto 600 g", "presunto", quantity={"value": 6, "unit": "kg"})
    items.append(item("Something else entirely", "banana"))
    del items[7]  # drops "Lanches das crianças"
    report = compare(FIXTURE, items)

    assert report.lines[6].produced is None and not report.lines[6].split_ok
    assert report.missed == ["lanches das crianças"]
    assert report.extras == ["banana"]
    assert report.brand_ok == report.found - 1
    assert report.constraints_ok == report.found - 1  # mix missing
    assert report.quantity_ok == report.found - 1
    assert len(report.problems) == 3


def test_duplicate_lines_are_claimed_in_order():
    items = [i for i in perfect() if i.name in ("requeijão", "margarina")]
    report = compare(FIXTURE, items)
    assert [line.produced for line in report.lines if line.raw == "Requeijão"] == [1, 1]
