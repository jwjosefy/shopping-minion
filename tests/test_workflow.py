"""The workflow steps with fakes: no browser, no Jev, no terminal."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from shopping_minion import workflow
from shopping_minion.config import DecideConfig
from shopping_minion.items import Candidate, CartResult, Decision, Item, Quantity
from shopping_minion.workflow import (
    DraftEdit,
    decide_list,
    draft_cart,
    edit_draft,
    fill_cart,
    search_list,
)

CONFIG = DecideConfig(model="jev-latest", batch_size=5, accept_at=0.8, ask_below=0.5)


def cand(pid, name, unit="un", step_kg=None, price="10.00"):
    return Candidate(
        product_id=pid,
        slug=f"slug-{pid}",
        name=name,
        brand=None,
        price=None if price is None else Decimal(price),
        list_price=None,
        unit_of_sale=unit,
        step_kg=step_kg,
        available=True,
    )


def item(name, quantity=None):
    return Item(source_line=name, name=name, search_term=name, quantity=quantity)


def decision(it, candidates, choice, status="accepted"):
    return Decision(
        item=it,
        candidates=candidates,
        choice=choice,
        confidence=0.9 if status == "accepted" else None,
        status=status,
    )


ATUM = cand("1", "Atum Gomes 170g", price="13.98")
REQUEIJAO = cand("2", "Requeijão Catupiry 200g", price="8.00")
FRANGO = cand("3", "Filé de Frango kg", unit="kg", step_kg=0.1, price="27.99")


class FakeClient:
    def __init__(self, answers):
        self.answers = answers

    def system_one(self, state, questions, model=None):
        choices = {}
        for key, question in questions.items():
            label, confidence = self.answers[question.instructions["item"]["name"]]
            choices[key] = SimpleNamespace(
                choice=label, confidence=confidence, probabilities={label: confidence}
            )
        return SimpleNamespace(choices=choices, model="jev-1.13.0")


# --- search_list and decide_list --------------------------------------------------------------


def test_search_list_passes_the_page_and_progress_through(monkeypatch):
    seen = {}

    def fake_search_all(page, items, progress):
        seen["page"] = page
        for i, it in enumerate(items, start=1):
            progress(i, len(items), it, [ATUM])
        return [[ATUM] for _ in items]

    monkeypatch.setattr(workflow, "search_all", fake_search_all)
    events = []
    items = [item("atum"), item("requeijão")]
    result = search_list("page", items, lambda *args: events.append(args))
    assert result == [[ATUM], [ATUM]]
    assert seen["page"] == "page"
    assert events == [(1, 2, items[0], [ATUM]), (2, 2, items[1], [ATUM])]


def test_decide_list_returns_a_decision_per_item_in_order():
    items = [item("atum"), item("requeijão"), item("nada")]
    candidates = [[ATUM], [REQUEIJAO], []]
    client = FakeClient({"atum": ("p1", 0.9), "requeijão": ("p2", 0.6)})
    decisions = decide_list(items, candidates, {}, CONFIG, client)
    assert [d.item.name for d in decisions] == ["atum", "requeijão", "nada"]
    assert [d.status for d in decisions] == ["accepted", "ask", "no_match"]
    assert decisions[0].choice == "1"


def test_decide_list_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        decide_list([item("atum")], [], {}, CONFIG, FakeClient({}))


# --- draft_cart -------------------------------------------------------------------------------


def test_draft_cart_targets_flags_and_estimates():
    decisions = [
        decision(item("atum"), [ATUM], "1"),
        decision(item("frango", Quantity(value=1, unit="kg")), [FRANGO], "3"),
        decision(
            item("frango 250", Quantity(value=250, unit="g")),
            [cand("4", "Outro kg", "kg", 0.1)],
            "4",
        ),
    ]
    draft = draft_cart(decisions, {})
    atum, frango, outro = draft.lines
    assert (atum.line_id, atum.target.clicks, atum.flags) == ("1", 1, ["QUANTITY_ASSUMED"])
    assert atum.estimated_price == Decimal("13.98")
    assert (frango.target.clicks, frango.flags) == (10, [])
    assert frango.quantity == Quantity(value=1, unit="kg")
    assert frango.estimated_price == Decimal("27.99")  # 27.99 x (10 x 0.1)
    assert (outro.target.clicks, outro.flags) == (3, ["QUANTITY_INEXACT"])
    assert outro.estimated_price == Decimal("3.00")  # 10.00 x (3 x 0.1)
    assert draft.estimated_total == Decimal("13.98") + Decimal("27.99") + Decimal("3.00")
    assert draft.skipped == []


def test_draft_cart_uses_preferences_for_the_quantity():
    prefs = {"atum": {"quantidade": {"valor": 3, "unidade": "un"}}}
    (line,) = draft_cart([decision(item("atum"), [ATUM], "1")], prefs).lines
    assert line.target.clicks == 3 and line.flags == []
    assert line.estimated_price == Decimal("41.94")


def test_draft_cart_skips_unresolved_and_skipped_decisions():
    decisions = [
        decision(item("atum"), [ATUM], "1"),
        decision(item("papel"), [ATUM], None, status="skipped"),
        decision(item("nada"), [], None, status="no_match"),
        decision(item("ask"), [ATUM], "1", status="ask"),
    ]
    draft = draft_cart(decisions, {})
    assert [line.line_id for line in draft.lines] == ["1"]
    assert [d.item.name for d in draft.skipped] == ["papel", "nada", "ask"]


def test_draft_cart_merges_the_requeijao_duplicates():
    decisions = [
        decision(item("requeijão"), [REQUEIJAO], "2"),
        decision(item("atum"), [ATUM], "1"),
        decision(item("requeijão"), [REQUEIJAO], "2", status="user_chosen"),
    ]
    draft = draft_cart(decisions, {})
    assert [line.line_id for line in draft.lines] == ["2", "1"]
    requeijao = draft.lines[0]
    assert requeijao.target.clicks == 2
    assert len(requeijao.items) == 2
    assert requeijao.estimated_price == Decimal("16.00")
    assert draft.estimated_total == Decimal("29.98")


def test_estimated_price_and_total_are_none_without_a_price():
    no_price = cand("5", "Sem preço", price=None)
    draft = draft_cart(
        [decision(item("x"), [no_price], "5"), decision(item("atum"), [ATUM], "1")], {}
    )
    assert draft.lines[0].estimated_price is None
    assert draft.lines[1].estimated_price == Decimal("13.98")
    assert draft.estimated_total is None


def test_a_kg_product_without_a_step_is_skipped_with_a_warning():
    broken = cand("6", "Sem passo kg", unit="kg", step_kg=None)
    draft = draft_cart([decision(item("x", Quantity(value=1, unit="kg")), [broken], "6")], {})
    assert draft.lines == []
    assert [d.item.name for d in draft.skipped] == ["x"]
    assert "step_kg" in draft.warnings[0] and draft.warnings[0].startswith("x:")
    assert draft.estimated_total is None


# --- edit_draft -------------------------------------------------------------------------------


def _draft():
    return draft_cart(
        [
            decision(item("atum"), [ATUM], "1"),
            decision(item("frango", Quantity(value=1, unit="kg")), [FRANGO], "3"),
            decision(item("papel"), [], None, status="skipped"),
        ],
        {},
    )


def test_edit_quantity_recomputes_clicks_flags_and_total():
    draft = _draft()
    edited = edit_draft(
        draft,
        [
            DraftEdit(line_id="1", quantity=Quantity(value=3, unit="un")),
            DraftEdit(line_id="3", quantity=Quantity(value=250, unit="g")),
        ],
    )
    atum, frango = edited.lines
    assert (atum.target.clicks, atum.flags, atum.target.flags) == (3, [], [])  # no longer assumed
    assert atum.quantity == Quantity(value=3, unit="un")
    assert atum.estimated_price == Decimal("41.94")
    assert (frango.target.clicks, frango.flags) == (3, ["QUANTITY_INEXACT"])
    assert frango.estimated_price == Decimal("8.40")  # 27.99 x 0.3
    assert edited.estimated_total == Decimal("50.34")
    assert draft.lines[0].target.clicks == 1  # the original draft is untouched


def test_edit_remove_moves_the_items_to_skipped():
    draft = draft_cart(
        [
            decision(item("requeijão"), [REQUEIJAO], "2"),
            decision(item("requeijão"), [REQUEIJAO], "2"),
        ],
        {},
    )
    edited = edit_draft(draft, [DraftEdit(line_id="2", remove=True)])
    assert edited.lines == []
    assert [d.item.name for d in edited.skipped] == ["requeijão", "requeijão"]
    assert all(d.status == "skipped" and d.choice is None for d in edited.skipped)
    assert edited.estimated_total is None


def test_edit_without_quantity_or_remove_changes_nothing():
    draft = _draft()
    assert edit_draft(draft, [DraftEdit(line_id="1")]) == draft
    assert edit_draft(draft, []) == draft


def test_edit_of_an_unknown_line_raises():
    with pytest.raises(ValueError, match="nope"):
        edit_draft(_draft(), [DraftEdit(line_id="nope", remove=True)])


# --- fill_cart --------------------------------------------------------------------------------


class FakeCart:
    """Stands in for the drawer and for add_all; records the order of the calls."""

    def __init__(self, monkeypatch, reads):
        self.reads = list(reads)
        self.calls = []
        monkeypatch.setattr(workflow, "read_cart_drawer", self.read)
        monkeypatch.setattr(workflow, "add_all", self.add)

    def read(self, page):
        self.calls.append("read")
        value = self.reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def add(self, page, targets, progress=None):
        self.calls.append("add")
        self.targets = targets
        results = []
        for i, (candidate, target) in enumerate(targets, start=1):
            result = CartResult(
                product_id=candidate.product_id,
                status="added",
                quantity_shown=str(target.clicks),
                message=None,
            )
            results.append(result)
            if progress:
                progress(i, len(targets), candidate, result)
        return results


def test_fill_cart_reads_adds_reads_and_reconciles(monkeypatch):
    cart = FakeCart(
        monkeypatch,
        [
            [("Algo Antigo", "1")],
            [("Algo Antigo", "1"), ("Atum Gomes 170g", "1"), ("Filé de Frango kg", "1kg")],
        ],
    )
    draft = draft_cart(
        [
            decision(item("atum"), [ATUM], "1"),
            decision(item("frango", Quantity(value=1, unit="kg")), [FRANGO], "3"),
        ],
        {},
    )
    progress = []
    outcome = fill_cart("page", draft, lambda *args: progress.append(args))
    assert cart.calls == ["read", "add", "read"]
    assert [(c.product_id, t.clicks) for c, t in cart.targets] == [("1", 1), ("3", 10)]
    assert outcome.before == [("Algo Antigo", "1")]
    assert [r.status for r in outcome.results] == ["added", "added"]
    assert [(i, n, c.name) for i, n, c, _ in progress] == [
        (1, 2, "Atum Gomes 170g"),
        (2, 2, "Filé de Frango kg"),
    ]
    assert [c.item_name for c in outcome.checks] == ["atum", "frango"]
    assert all(c.ok for c in outcome.checks)
    assert outcome.extras == [("Algo Antigo", "1")]
    assert outcome.before_error is None and outcome.after_error is None


def test_fill_cart_expects_the_summed_quantity_of_a_merged_line(monkeypatch):
    draft = draft_cart(
        [
            decision(item("requeijão"), [REQUEIJAO], "2"),
            decision(item("requeijão"), [REQUEIJAO], "2"),
        ],
        {},
    )
    FakeCart(monkeypatch, [[], [("Requeijão Catupiry 200g", "1")]])  # the old bug: 1, not 2
    outcome = fill_cart("page", draft)
    (check,) = outcome.checks
    assert check.item_name == "requeijão ×2"
    assert check.expected == "2"
    assert not check.ok
    assert check.verdict == "QUANTIDADE DIFERENTE: carrinho tem 1, esperado 2"

    FakeCart(monkeypatch, [[], [("Requeijão Catupiry 200g", "2")]])
    (check,) = fill_cart("page", draft).checks
    assert check.ok


def test_fill_cart_goes_on_when_the_cart_cannot_be_read(monkeypatch):
    draft = draft_cart([decision(item("atum"), [ATUM], "1")], {})
    FakeCart(monkeypatch, [RuntimeError("gaveta não abriu"), [("Atum Gomes 170g", "1")]])
    outcome = fill_cart("page", draft)
    assert outcome.before is None and outcome.before_error == "gaveta não abriu"
    assert outcome.checks[0].ok and outcome.checks[0].was_before is False

    FakeCart(monkeypatch, [[], RuntimeError("sem gaveta")])
    outcome = fill_cart("page", draft)
    assert len(outcome.results) == 1  # the products were still added
    assert outcome.after is None and outcome.after_error == "sem gaveta"
    assert outcome.checks == [] and outcome.extras == []


def test_workflow_has_no_terminal_io():
    source = Path(workflow.__file__).read_text(encoding="utf-8")
    assert "input(" not in source and "print(" not in source
