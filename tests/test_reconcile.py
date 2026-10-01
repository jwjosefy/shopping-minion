from decimal import Decimal

from shopping_minion.items import Candidate, CartTarget
from shopping_minion.reconcile import cart_amount, format_amount, reconcile, report_lines


def candidate(name, unit="un", step=None):
    return Candidate(
        product_id=name,
        slug="s",
        name=name,
        brand=None,
        price=Decimal("1"),
        list_price=None,
        unit_of_sale=unit,
        step_kg=step,
        available=True,
    )


ATUM = candidate("Atum Sólido Coqueiro Natural 170g")
PAPEL = candidate("Papel Higiênico Fancy Folha Dupla 30m C/16 Rolos")
FRANGO = candidate("Filé De Peito Frango Resf Kg", unit="kg", step=0.1)
PLAN = [
    ("atum", ATUM, CartTarget(product_id=ATUM.product_id, clicks=1)),
    ("papel higiênico", PAPEL, CartTarget(product_id=PAPEL.product_id, clicks=1)),
    ("filé de peito de frango", FRANGO, CartTarget(product_id=FRANGO.product_id, clicks=10)),
]


def test_cart_amount():
    assert cart_amount("3") == (3, "un")
    assert cart_amount("300g") == (0.3, "kg")
    assert cart_amount("1kg") == (1, "kg")
    assert cart_amount("1,5kg") == (1.5, "kg")
    assert cart_amount("Instruções") is None
    assert format_amount((0.3, "kg")) == "300g"
    assert format_amount((1.0, "kg")) == "1kg"


def test_johanns_second_run():
    """2026-10-01: papel and frango were left from an earlier run, plus a stray atum."""
    before = [
        ("Atum Sólido Gomes Da Costa Natural 170g", "1"),
        ("Filé De Peito Frango Resf Kg", "1kg"),
        ("Papel Higiênico Fancy Folha Dupla 30m C/16 Rolos", "1"),
    ]
    after = [
        ("Atum Sólido Gomes Da Costa Natural 170g", "1"),
        ("Atum Sólido Coqueiro Natural 170g", "1"),
        ("Filé De Peito Frango Resf Kg", "1kg"),
        ("Papel Higiênico Fancy Folha Dupla 30m C/16 Rolos", "1"),
    ]
    checks, extras = reconcile(PLAN, before, after)
    assert [c.ok for c in checks] == [True, True, True]
    assert [c.was_before for c in checks] == [False, True, True]
    assert extras == [("Atum Sólido Gomes Da Costa Natural 170g", "1")]
    lines = report_lines(checks, extras, before)
    assert "  3 de 3 itens da lista conferem." in lines
    assert "!! 1 produto(s) no carrinho que NÃO são desta lista:" in lines
    assert "     1  Atum Sólido Gomes Da Costa Natural 170g (já estava antes desta rodada)" in lines


def test_missing_and_wrong_quantity():
    after = [("Filé De Peito Frango Resf Kg", "300g"), ("Atum Sólido Coqueiro Natural 170g", "2")]
    checks, extras = reconcile(PLAN, [], after)
    verdicts = {c.item_name: c.verdict for c in checks}
    assert verdicts["papel higiênico"] == "FALTANDO no carrinho"
    assert verdicts["atum"] == "QUANTIDADE DIFERENTE: carrinho tem 2, esperado 1"
    assert verdicts["filé de peito de frango"] == (
        "QUANTIDADE DIFERENTE: carrinho tem 300g, esperado 1kg"
    )
    assert extras == []


def test_unknown_before_is_said():
    checks, extras = reconcile(PLAN, None, [])
    assert any("não pôde ser lido antes" in line for line in report_lines(checks, extras, None))
