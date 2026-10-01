import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from shopping_minion.config import DecideConfig
from shopping_minion.items import Candidate, Decision, Item

EVALS = Path(__file__).parent.parent / "evals"
spec = importlib.util.spec_from_file_location("decide_eval", EVALS / "decide_eval.py")
decide_eval = importlib.util.module_from_spec(spec)
sys.modules["decide_eval"] = decide_eval
spec.loader.exec_module(decide_eval)

CONFIG = DecideConfig(model="jev-1.13", batch_size=5, accept_at=0.8, ask_below=0.5)


def raw(kind="unit", step=None, pack=None, **kw):
    base = {
        "id": 123,
        "name": "Atum Gomes 170g",
        "brand": "Gomes",
        "size": None,
        "unit_of_sale": {"kind": kind, "step_size_g": step, "pack_size": pack},
        "price": "9.50",
        "in_stock": True,
        "url": "https://example.test/busca/x",
    }
    return base | kw


def test_convert_unit_candidate():
    c = decide_eval.convert_candidate(raw())
    assert c.product_id == "123"
    assert c.slug == "atum-gomes-170g"
    assert c.price == Decimal("9.50")
    assert c.unit_of_sale == "un" and c.step_kg is None
    assert c.available and c.list_price is None


def test_convert_pack_is_sold_by_unit():
    assert decide_eval.convert_candidate(raw("pack", pack=12)).unit_of_sale == "un"


def test_convert_weight_step_is_kg_with_step():
    c = decide_eval.convert_candidate(raw("weight_step", step=300.0))
    assert c.unit_of_sale == "kg"
    assert c.step_kg == pytest.approx(0.3)


def test_convert_out_of_stock_and_no_brand():
    c = decide_eval.convert_candidate(raw(in_stock=False, brand=None, name="Filé de Frango Kg"))
    assert not c.available and c.brand is None
    assert c.slug == "file-de-frango-kg"


def test_load_real_fixtures():
    cases = decide_eval.load_cases()
    assert [c.id for c in cases][:2] == ["atum", "papel-higienico"]
    assert len(cases) == 7
    assert all(len(c.candidates) == 20 for c in cases)
    presunto = next(c for c in cases if c.id == "presunto-600g")
    assert presunto.item.quantity.value == 600 and presunto.item.quantity.unit == "g"
    feijao = next(c for c in cases if c.id == "feijao-normal-nao-preto")
    assert feijao.item.constraints == ["normal", "não preto"]
    assert feijao.item.source_line == "Feijão normal / preto não"
    assert next(c for c in cases if c.id == "atum-among-milk").expect_none
    assert next(c for c in cases if c.id == "atum").item.source_line == "atum"


def test_max_candidates():
    assert all(len(c.candidates) == 5 for c in decide_eval.load_cases(max_candidates=5))


def case(accept=(), reject=(), expect_none=False):
    item = Item(source_line="x", name="x", search_term="x")
    return decide_eval.Case("c", item, [], list(accept), list(reject), expect_none)


def test_labels_ignore_case_and_accents():
    c = case(accept=["papel higienico"], reject=["toalha"])
    assert decide_eval.choice_is_correct(c, "PAPEL HIGIÊNICO Neve 12un")
    assert not decide_eval.choice_is_correct(c, "Toalha de papel")
    assert not decide_eval.choice_is_correct(c, "Guardanapo")


def test_reject_wins_over_accept():
    c = case(accept=["feij"], reject=["preto"])
    assert decide_eval.choice_is_correct(c, "Feijão Carioca Camil")
    assert not decide_eval.choice_is_correct(c, "Feijão Preto Camil")


def test_accept_is_any_of():
    c = case(accept=["file.*peito", "frango.*file"])
    assert decide_eval.choice_is_correct(c, "Frango Filé Kg")


def test_nenhum_is_wrong_unless_expect_none():
    assert not decide_eval.choice_is_correct(case(accept=["atum"]), None)
    assert decide_eval.choice_is_correct(case(expect_none=True), None)
    assert not decide_eval.choice_is_correct(case(expect_none=True), "Leite Integral")


def make_decision(c, name, status, confidence=0.9):
    candidate = Candidate(
        product_id="1",
        slug="s",
        name=name or "Qualquer",
        brand=None,
        price=None,
        list_price=None,
        unit_of_sale="un",
        step_kg=None,
        available=True,
    )
    return Decision(
        item=c.item,
        candidates=[candidate],
        choice=None if name is None else "1",
        confidence=confidence,
        status=status,
    )


def test_scoring_verdicts():
    atum = case(accept=["^atum"])
    score = decide_eval.score
    assert score(atum, make_decision(atum, "Atum Gomes", "accepted"), CONFIG).verdict == "ok"
    wrong = score(atum, make_decision(atum, "Sardinha", "accepted"), CONFIG)
    assert wrong.verdict == "WRONG" and not wrong.raw_correct
    asked = score(atum, make_decision(atum, "Atum Gomes", "ask", 0.6), CONFIG)
    assert asked.verdict == "ask" and asked.raw_correct and not asked.flag_nothing_fit
    assert score(atum, make_decision(atum, "Atum", "ask", 0.3), CONFIG).flag_nothing_fit
    none_case = case(expect_none=True)
    assert score(none_case, make_decision(none_case, None, "no_match"), CONFIG).verdict == "ok"
    assert score(none_case, make_decision(none_case, "Leite", "ask", 0.6), CONFIG).verdict == "ok"
    accepted_milk = make_decision(none_case, "Leite", "accepted")
    assert score(none_case, accepted_milk, CONFIG).verdict == "WRONG"


class TunaClient:
    """Picks the first tuna product when there is one, else `nenhum`."""

    def system_one(self, state, questions, model=None):
        choices = {}
        for key, question in questions.items():
            tuna = [k for k, v in question.criteria.items() if "atum" in v.lower()]
            if tuna:
                pick = tuna[0]
                choices[key] = SimpleNamespace(
                    choice=pick, confidence=0.95, probabilities={pick: 0.95, "nenhum": 0.05}
                )
            else:
                choices[key] = SimpleNamespace(
                    choice="nenhum", confidence=0.9, probabilities={"nenhum": 0.9}
                )
        return SimpleNamespace(choices=choices)


def test_run_and_totals_with_fake_client():
    outcomes = decide_eval.run(CONFIG, decide_eval.load_cases(), TunaClient())
    by_id = {o.case.id: o for o in outcomes}
    assert by_id["atum"].verdict == "ok"
    assert by_id["atum-among-milk"].verdict == "ok"  # "nenhum" on a no-match case
    assert by_id["leite"].verdict == "ask" and not by_id["leite"].raw_correct
    assert decide_eval.totals(outcomes) == {
        "cases": 7,
        "correct after policy": 2,
        "asked (user decides)": 5,
        "asked, pick correct": 0,
        "raw picks correct": 2,
        "wrong product would be added": 0,
    }


def test_main_requires_api_key(monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert decide_eval.main([]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_print_report(capsys):
    outcomes = decide_eval.run(CONFIG, decide_eval.load_cases(), TunaClient())
    decide_eval.print_report(CONFIG, outcomes)
    out = capsys.readouterr().out
    assert "batch_size=5" in out
    assert "wrong product would be added: 0" in out
    assert out.count("status=") == 7
