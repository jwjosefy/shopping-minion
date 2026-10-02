import importlib.util
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from shopping_minion.config import DecideConfig
from shopping_minion.decide import HISTORY_OPTIONS_RULE
from shopping_minion.items import Candidate, Decision, Item
from shopping_minion.orders import Order, OrderLine
from shopping_minion.storage import Storage

EVALS = Path(__file__).parent.parent / "evals"
spec = importlib.util.spec_from_file_location("history_eval", EVALS / "history_eval.py")
history_eval = importlib.util.module_from_spec(spec)
sys.modules["history_eval"] = history_eval
spec.loader.exec_module(history_eval)

CONFIG = DecideConfig(model="jev-test", batch_size=5, accept_at=0.8, ask_below=0.5)


def cand(pid, name):
    return Candidate(
        product_id=pid,
        slug="x",
        name=name,
        brand=None,
        price=Decimal("5"),
        list_price=None,
        unit_of_sale="un",
        step_kg=None,
        available=True,
    )


def decision(name, candidates, choice, status, confidence=None):
    item = Item(source_line=name, name=name, search_term=name)
    return Decision(
        item=item, candidates=candidates, choice=choice, confidence=confidence, status=status
    )


def line(order_id, day, pid, name, year=2026, month=9):
    return OrderLine(
        order_id=order_id,
        placed_at=datetime(year, month, day, 12, 0, tzinfo=UTC),
        product_id=pid,
        name=name,
        quantity=1,
        unit="un",
        total_price=None,
    )


def order(order_id, lines):
    return Order(
        order_id=order_id,
        placed_at=lines[0].placed_at,
        status="FINISHED",
        total=None,
        lines=lines,
    )


LEITE = [cand("1", "Leite Integral 1L"), cand("2", "Leite Desnatado 1L")]
ARROZ = [cand("2", "Arroz Tipo 1"), cand("3", "Arroz Integral"), cand("4", "Arroz Parboilizado")]
FEIJAO = [cand("5", "Feijao Carioca"), cand("6", "Feijao Preto")]
SAL = [cand("11", "Sal Refinado")]
CAFE = [cand("7", "Cafe Torrado"), cand("8", "Cafe Solúvel")]
LARANJA = [cand("9", "Laranja Pera Kg"), cand("10", "Laranja Bahia Kg")]


@pytest.fixture
def db(tmp_path):
    """A made-up run 1: six items with candidates and one without, plus orders."""
    path = tmp_path / "test.sqlite"
    storage = Storage(path)
    run_id = storage.new_run(None)
    assert run_id == 1
    storage.save_decisions(
        run_id,
        [
            decision("leite", LEITE, "1", "accepted", 0.9),
            decision("arroz", ARROZ, "3", "user_chosen"),
            decision("feijao", FEIJAO, "5", "user_chosen"),
            decision("sal", SAL, None, "skipped"),
            decision("cafe", CAFE, "7", "accepted", 0.9),
            decision("pimenta", [], None, "no_match"),
            decision("laranja", LARANJA, "9", "user_chosen"),
        ],
    )
    log = [
        (1, "arroz", "2", 0.6, "3"),
        (2, "feijao", "5", 0.6, "5"),
        (3, "sal", "11", 0.6, None),
        (6, "laranja", "10", 0.6, "9"),
    ]
    for index, name, jev, confidence, chosen in log:
        storage.log(
            run_id,
            "pick",
            {
                "index": index,
                "item": name,
                "jev_choice": jev,
                "jev_confidence": confidence,
                "chosen": chosen,
            },
        )
    storage.log(run_id, "cart_edit", {"line_id": "7", "quantity": None, "remove": True})
    storage.log(
        run_id,
        "cart_edit",
        {"line_id": "1", "quantity": {"value": 2, "unit": "un"}, "remove": False},
    )
    storage.save_order(
        order(
            "o1",
            [line("o1", 10, "1", "Leite Integral 1L"), line("o1", 10, "3", "Arroz Integral")],
        )
    )
    storage.save_order(
        order(
            "o2",
            [line("o2", 20, "1", "Leite Integral 1L"), line("o2", 20, "99", "Laranja Pera Kg 1")],
        )
    )
    # placed after the run: it holds the answer for "feijao" and must not be used
    storage.save_order(order("o3", [line("o3", 1, "5", "Feijao Carioca", year=2099, month=1)]))
    storage.close()
    return path


def load(db_path):
    storage = Storage(db_path)
    try:
        decisions = storage.read_decisions(1)
        log = storage.read_log(1)
        lines = storage.order_lines(before=datetime.now(UTC))
    finally:
        storage.close()
    return history_eval.build_rows(decisions, log, lines, related_lines=10), log


def test_ground_truth(db):
    rows, _ = load(db)
    assert [(r.final, r.outcome) for r in rows] == [
        ("1", "decided"),  # accepted, and the other cart_edit only changed a quantity
        ("3", "decided"),  # user_chosen
        ("5", "decided"),
        (None, "skipped"),
        (None, "removed"),  # accepted, then its line was removed
        (None, "no_match"),
        ("9", "decided"),
    ]


def test_history_uses_only_orders_before_the_run(db):
    rows, _ = load(db)
    by_name = {r.item.name: r for r in rows}
    assert by_name["leite"].history.products["1"].orders == 2
    assert [r.has_history for r in rows] == [True, True, False, False, False, False, False]
    assert not by_name["feijao"].has_history  # the 2099 order is ignored
    assert [ln.product_id for ln in by_name["laranja"].history.related] == ["99"]


def test_exclusions_and_the_history_gap(db):
    rows, _ = load(db)
    picks = history_eval.live_picks(rows, load(db)[1])
    summary = history_eval.run_summary(rows, picks)
    assert summary["decided"] == 4
    assert summary["excluded"] == {"skipped": 1, "no_match": 1, "removed": 1}
    assert summary["decided_with_history"] == 2
    assert summary["final_without_history_but_related"] == 1  # laranja
    near = summary["near_misses"]
    assert [(n["item"], n["line"], n["candidate"]) for n in near] == [
        ("laranja", "Laranja Pera Kg 1", "Laranja Pera Kg")
    ]
    assert near[0]["candidate_is_final"]


def test_live_only_reads_the_pick_rows(db):
    rows, log = load(db)
    picks = history_eval.live_picks(rows, log)
    assert picks[0] == history_eval.Pick("1", accepted=True)
    assert picks[1] == history_eval.Pick("2", accepted=False)  # Jev's pick, not the user's
    assert picks[3] == history_eval.Pick("11", accepted=False)
    assert picks[4] == history_eval.Pick("7", accepted=True)  # cafe
    assert 5 not in picks  # pimenta: no candidates, no pick
    m = history_eval.score(rows, picks)
    assert m["with_history"] == {"hits": 1, "decided": 2, "rate": 0.5}
    assert m["without_history"] == {"hits": 1, "decided": 2, "rate": 0.5}
    assert m["overall"]["hits"] == 2 and m["overall"]["decided"] == 4
    assert (m["accepted"], m["accepted_misses"], m["picker"]) == (1, 0, 3)
    assert [(x["item"], x["jev_pick"], x["final"]) for x in m["misses"]] == [
        ("arroz", "Arroz Tipo 1", "Arroz Integral"),
        ("laranja", "Laranja Bahia Kg", "Laranja Pera Kg"),
    ]


class FakeClient:
    """Answers from `answers[item name]`; `options` answers differ where `by_mode` says so."""

    def __init__(self, answers, options=None):
        self.answers = answers
        self.options = options or {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def system_one(self, state, questions, model=None):
        self.calls.append(questions)
        choices = {}
        for key, question in questions.items():
            name = question.instructions["item"]["name"]
            table = (
                self.options if HISTORY_OPTIONS_RULE in question.instructions["question"] else {}
            )
            label, confidence, probabilities = table.get(name) or self.answers[name]
            choices[key] = SimpleNamespace(
                choice=label, confidence=confidence, probabilities=probabilities
            )
        return SimpleNamespace(choices=choices)


def fake_client():
    answers = {
        "leite": ("p1", 0.9, {"p1": 0.9, "p2": 0.05, "nenhum": 0.05}),
        "arroz": ("p2", 0.6, {"p2": 0.5, "p3": 0.4, "p4": 0.05, "nenhum": 0.05}),
        "feijao": ("p5", 0.6, {"p5": 0.6, "p6": 0.3, "nenhum": 0.1}),
        "sal": ("p11", 0.6, {"p11": 0.6, "nenhum": 0.4}),
        "cafe": ("p7", 0.9, {"p7": 0.9, "p8": 0.05, "nenhum": 0.05}),
        "laranja": ("p10", 0.9, {"p10": 0.9, "p9": 0.05, "nenhum": 0.05}),
    }
    options = {"arroz": ("p3", 0.85, {"p3": 0.85, "p2": 0.1, "nenhum": 0.05})}
    return FakeClient(answers, options)


def test_replay_variants_and_variant_c(db):
    rows, _ = load(db)
    client = fake_client()
    by_variant = history_eval.replay(rows, CONFIG, ["none", "options"], [0.5, 1, 2], client, {})
    assert list(by_variant) == ["none", "options", "prior a=0.5", "prior a=1", "prior a=2"]
    # 6 asked items ("pimenta" has no candidates) in batches of 5, per variant; C adds none
    assert sorted(len(q) for q in client.calls) == [1, 1, 5, 5]

    none = history_eval.score(rows, by_variant["none"])
    assert none["with_history"] == {"hits": 1, "decided": 2, "rate": 0.5}
    assert (none["accepted"], none["accepted_misses"], none["picker"]) == (2, 1, 2)

    options = history_eval.score(rows, by_variant["options"])
    assert options["with_history"]["hits"] == 2
    assert (options["accepted"], options["accepted_misses"]) == (3, 1)

    # arroz: p3 was bought once, weight 1 + a. At 0.5: 0.4 x 1.5 = 0.6 beats p2 at 0.5
    prior = by_variant["prior a=0.5"][1]
    assert prior.choice == "3" and not prior.accepted  # 0.6 / 1.2 = 0.5 < accept_at
    assert by_variant["prior a=0.5"][0].accepted  # leite stays accepted (bought twice)
    # at alpha 2, p3 gets 1.2 of 1.8: still below accept_at
    assert not by_variant["prior a=2"][1].accepted


def test_reweight_caps_orders_at_three_and_normalizes():
    out = history_eval.reweight({"1": 0.5, "2": 0.5, "nenhum": 0.0}, {"1": 10}, alpha=1)
    assert out["1"] == pytest.approx(4 / 5)  # 0.5 x (1 + 3) = 2 against 0.5
    assert out["2"] == pytest.approx(1 / 5)
    assert sum(out.values()) == pytest.approx(1)


def prior_row(base_choice, probabilities, orders=0):
    candidates = [cand("1", "A"), cand("2", "B")]
    row = history_eval.Row(
        index=0,
        decision=decision("x", candidates, base_choice, "ask", 0.4),
        final=None,
        outcome="no_match",
        history=history_eval.ItemHistory(products={}, related=[]),
    )
    base = row.decision.model_copy(update={"probabilities": probabilities})
    return row, base


def test_variant_c_ties_keep_the_none_choice_then_search_order():
    tied = {"1": 0.4, "2": 0.4, "nenhum": 0.2}
    row, base = prior_row("2", tied)
    assert history_eval.prior_pick(row, base, 1.0, CONFIG).choice == "2"
    row, base = prior_row(None, tied)  # the none choice is not among the tied: search order
    assert history_eval.prior_pick(row, base, 1.0, CONFIG).choice == "1"
    row, base = prior_row(None, {"1": 0.1, "2": 0.1, "nenhum": 0.8})
    pick = history_eval.prior_pick(row, base, 1.0, CONFIG)
    assert pick.choice is None and not pick.accepted  # `nenhum` is a miss, never an accept


def test_main_replay_writes_json(db, tmp_path, capsys):
    out = tmp_path / "out" / "r.json"
    code = history_eval.main(
        [
            "--run", "1", "--db", str(db), "--out", str(out), "--alpha", "1",
            "--preferences", str(tmp_path / "none.yaml"),
        ],
        client_factory=lambda model: fake_client(),
    )  # fmt: skip
    assert code == 0
    printed = capsys.readouterr().out
    assert "3 variants (none, options, list) x 6 questions = 18 questions" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["mode"] == "replay"
    assert list(saved["variants"]) == ["none", "options", "list", "prior a=1"]
    assert saved["variants"]["none"]["overall"]["decided"] == 4


def test_main_live_only_makes_no_client(db, tmp_path):
    out = tmp_path / "live.json"

    def forbidden(model):
        raise AssertionError("no Jev call in --live-only")

    code = history_eval.main(
        ["--run", "1", "--db", str(db), "--out", str(out), "--live-only"],
        client_factory=forbidden,
    )
    assert code == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["mode"] == "live-only" and list(saved["variants"]) == ["live"]


def test_unknown_run_is_an_error(db, tmp_path):
    with pytest.raises(SystemExit):
        history_eval.main(["--run", "9", "--db", str(db), "--live-only"])
