from decimal import Decimal

from shopping_minion.items import Candidate, Item, Quantity
from shopping_minion.merge import build_line, line_label, merge_lines


def cand(pid, name="Produto", unit="un", step_kg=None, price="10.00"):
    return Candidate(
        product_id=pid,
        slug=f"slug-{pid}",
        name=name,
        brand=None,
        price=Decimal(price),
        list_price=None,
        unit_of_sale=unit,
        step_kg=step_kg,
        available=True,
    )


def item(name, quantity=None):
    return Item(source_line=name, name=name, search_term=name, quantity=quantity)


def q(value, unit):
    return Quantity(value=value, unit=unit)


REQUEIJAO = cand("1", "Requeijão Catupiry 200g", price="8.00")
FRANGO = cand("2", "Filé de Frango kg", unit="kg", step_kg=0.1, price="20.00")


def single(candidate, name, quantity=None):
    """A one-item line as draft_cart builds it: no quantity means 1 un, assumed."""
    return build_line(
        candidate, [item(name, quantity)], quantity or q(1, "un"), assumed=quantity is None
    )


def test_requeijao_twice_is_one_line_with_two_clicks():
    lines = merge_lines([single(REQUEIJAO, "requeijão"), single(REQUEIJAO, "requeijão")])
    (line,) = lines
    assert line.line_id == "1"
    assert line.target.clicks == 2
    assert line.quantity == q(2, "un")
    assert line.flags == line.target.flags == ["QUANTITY_ASSUMED"]
    assert [i.name for i in line.items] == ["requeijão", "requeijão"]
    assert line.estimated_price == Decimal("16.00")
    assert line_label(line) == "requeijão ×2"


def test_count_units_add_up_in_units():
    lines = merge_lines([single(REQUEIJAO, "a", q(1, "dz")), single(REQUEIJAO, "b", q(2, "pct"))])
    (line,) = lines
    assert line.quantity == q(14, "un")
    assert line.target.clicks == 14
    assert line.flags == []


def test_kg_plus_kg_sums_the_quantity_before_converting():
    # 250 g + 250 g on a 0.1 kg stepper: 3 + 3 clicks one by one, 5 clicks as a sum
    a = single(FRANGO, "frango", q(250, "g"))
    b = single(FRANGO, "frango", q(250, "g"))
    assert a.flags == ["QUANTITY_INEXACT"]
    (line,) = merge_lines([a, b])
    assert line.quantity == q(0.5, "kg")
    assert line.target.clicks == 5
    assert line.flags == []  # the sum is exact
    assert line.estimated_price == Decimal("10.00")


def test_kg_plus_kg_still_inexact_when_the_sum_is():
    a = single(FRANGO, "frango", q(1, "kg"))
    b = single(FRANGO, "frango", q(50, "g"))
    (line,) = merge_lines([a, b])
    assert line.quantity == q(1.05, "kg")
    assert line.target.clicks == 11
    assert line.flags == ["QUANTITY_INEXACT"]


def test_count_plus_weight_takes_the_larger_clicks_and_is_inexact():
    a = single(FRANGO, "frango", q(2, "un"))  # count on a kg product: 1 click, inexact
    b = single(FRANGO, "frango", q(500, "g"))  # 5 clicks
    (line,) = merge_lines([a, b])
    assert line.target.clicks == 5
    assert line.quantity == q(500, "g")
    assert line.flags == ["QUANTITY_INEXACT"]
    assert line.target.flags == ["QUANTITY_INEXACT"]
    assert len(line.items) == 2
    assert line.estimated_price == Decimal("10.00")


def test_un_plus_kg_on_a_unit_product():
    a = single(REQUEIJAO, "requeijão", q(3, "un"))  # 3 clicks
    b = single(REQUEIJAO, "requeijão", q(500, "g"))  # weight on a unit product: 1 click
    (line,) = merge_lines([a, b])
    assert line.target.clicks == 3
    assert line.quantity == q(3, "un")
    assert line.flags == ["QUANTITY_INEXACT"]


def test_mixed_kinds_keep_the_assumed_flag():
    a = single(FRANGO, "frango")  # nothing on the list: 1 un, assumed
    b = single(FRANGO, "frango", q(300, "g"))  # 3 clicks
    (line,) = merge_lines([a, b])
    assert line.target.clicks == 3
    assert line.flags == ["QUANTITY_ASSUMED", "QUANTITY_INEXACT"]


def test_three_duplicates():
    lines = merge_lines(
        [
            single(REQUEIJAO, "requeijão"),
            single(REQUEIJAO, "requeijão", q(3, "un")),
            single(REQUEIJAO, "requeijão cremoso"),
        ]
    )
    (line,) = lines
    assert line.target.clicks == 5
    assert line.flags == ["QUANTITY_ASSUMED"]
    assert len(line.items) == 3
    assert line_label(line) == "requeijão + requeijão + requeijão cremoso"


def test_no_duplicates_leaves_the_lines_unchanged_and_in_order():
    lines = [single(FRANGO, "frango", q(250, "g")), single(REQUEIJAO, "requeijão")]
    assert merge_lines(lines) == lines
    assert [line_label(line) for line in lines] == ["frango", "requeijão"]


def test_merge_keeps_the_order_of_first_appearance():
    other = cand("3", "Atum")
    lines = merge_lines([single(REQUEIJAO, "r"), single(other, "atum"), single(REQUEIJAO, "r")])
    assert [line.line_id for line in lines] == ["1", "3"]
    assert lines[0].target.clicks == 2
    assert lines[1].target.clicks == 1


def test_estimated_price_is_none_without_a_price():
    no_price = cand("4", "Sem preço").model_copy(update={"price": None})
    assert single(no_price, "x").estimated_price is None


# --- history quantities ---


def from_history(candidate, name, quantity):
    return build_line(candidate, [item(name)], quantity, from_history=True)


def test_build_line_rounds_to_the_nearest_step_when_the_quantity_is_from_history():
    line = from_history(FRANGO, "frango", q(0.965, "kg"))
    assert line.target.clicks == 10
    assert line.flags == line.target.flags == ["QUANTITY_FROM_HISTORY"]
    nearby = from_history(cand("3", unit="kg", step_kg=0.5), "x", q(3.68, "kg"))
    assert nearby.target.clicks == 7  # 3.5 kg
    assert nearby.estimated_price == Decimal("35.00")


def test_build_line_rounds_up_without_history():
    line = build_line(cand("3", unit="kg", step_kg=0.5), [item("x")], q(3.68, "kg"))
    assert line.target.clicks == 8
    assert line.flags == ["QUANTITY_INEXACT"]


def test_a_merged_line_of_history_lines_rounds_to_the_nearest_step():
    # 0.12 + 0.12 = 0.24 kg on a 0.1 step: 2.4 steps
    lines = [from_history(FRANGO, "a", q(0.12, "kg")), from_history(FRANGO, "b", q(0.12, "kg"))]
    (line,) = merge_lines(lines)
    assert line.quantity == q(0.24, "kg")
    assert line.target.clicks == 2  # nearest; up would give 3
    assert line.flags == ["QUANTITY_FROM_HISTORY"]


def test_a_merged_line_rounds_up_if_any_source_is_not_from_history():
    lines = [from_history(FRANGO, "a", q(0.12, "kg")), single(FRANGO, "b", q(120, "g"))]
    (line,) = merge_lines(lines)
    assert line.target.clicks == 3  # 0.24 kg rounded up on a 0.1 step
    assert line.flags == ["QUANTITY_FROM_HISTORY", "QUANTITY_INEXACT"]
