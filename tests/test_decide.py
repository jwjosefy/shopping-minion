import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

from shopping_minion.config import DecideConfig
from shopping_minion.decide import (
    NONE_KEY,
    DecideError,
    build_questions,
    decide,
    describe_candidate,
    nothing_fit,
)
from shopping_minion.items import Candidate, Item


def cand(pid, name="Atum Gomes de Sá 170g", brand="Gomes", price="9.5", unit="un", available=True):
    return Candidate(
        product_id=pid,
        slug="x",
        name=name,
        brand=brand,
        price=Decimal(price) if price is not None else None,
        list_price=None,
        unit_of_sale=unit,
        step_kg=0.3 if unit == "kg" else None,
        available=available,
    )


def item(name="atum", **kw):
    return Item(source_line=kw.pop("source_line", name), name=name, search_term=name, **kw)


def config(batch_size=5, accept_at=0.8, ask_below=0.5):
    return DecideConfig(
        model="jev-1.13", batch_size=batch_size, accept_at=accept_at, ask_below=ask_below
    )


class FakeClient:
    """Answers each question from `answers[item name]`: (label, confidence, probabilities)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.threads = set()

    def system_one(self, state, questions, model=None):
        self.calls.append({"state": state, "questions": questions, "model": model})
        self.threads.add(threading.get_ident())
        choices = {}
        for key, question in questions.items():
            name = question.instructions["item"]["name"]
            label, confidence, probabilities = self.answers[name]
            choices[key] = SimpleNamespace(
                choice=label, confidence=confidence, probabilities=probabilities
            )
        return SimpleNamespace(choices=choices)


# --- questions -------------------------------------------------------------------------------


def test_question_keys_and_criteria():
    pairs = [(item("atum"), [cand("1"), cand("2", available=False)]), (item("leite"), [cand("3")])]
    questions = build_questions(pairs, {})
    assert list(questions) == ["item_0", "item_1"]
    assert list(questions["item_0"].criteria) == ["p1", "p2", NONE_KEY]
    assert list(questions["item_1"].criteria) == ["p3", NONE_KEY]
    assert all(q.type == "choice" for q in questions.values())


def test_instructions_carry_item_fields_and_reference_them_by_name():
    it = item(
        "feijão", constraints=["normal", "não preto"], source_line="Feijão normal / preto não"
    )
    q = build_questions([(it, [cand("1")])], {})["item_0"]
    assert q.instructions["item"] == {
        "name": "feijão",
        "constraints": ["normal", "não preto"],
        "source_line": "Feijão normal / preto não",
    }
    assert "`item`" in q.instructions["question"]
    assert "nenhum" in q.instructions["question"]
    assert "preferencia" not in q.instructions


def test_instructions_include_brand_and_preference_entry():
    prefs = {"feijão": {"variante": "carioca", "excluir": ["preto"]}}
    q = build_questions([(item("feijão", brand="Camil"), [cand("1")])], prefs)["item_0"]
    assert q.instructions["item"]["brand"] == "Camil"
    assert q.instructions["preferencia"] == {"variante": "carioca", "excluir": ["preto"]}
    assert "`preferencia`" in q.instructions["question"]


def test_preference_matches_by_alias():
    prefs = {"feijão": {"apelidos": ["feijão normal"], "variante": "carioca"}}
    q = build_questions([(item("feijão normal"), [cand("1")])], prefs)["item_0"]
    assert q.instructions["preferencia"]["variante"] == "carioca"


def test_candidate_descriptions():
    assert describe_candidate(cand("1")) == (
        "Atum Gomes de Sá 170g, marca Gomes, R$ 9,50, vendido por unidade"
    )
    kg = cand("2", name="Frango Kg", brand=None, price="1234.5", unit="kg", available=False)
    assert describe_candidate(kg) == "Frango Kg, R$ 1.234,50, vendido por kg, indisponível"
    assert "sem preço" in describe_candidate(cand("3", price=None))


def test_nenhum_criterion_says_when_it_is_right():
    q = build_questions([(item(), [cand("1")])], {})["item_0"]
    assert "Nenhum dos produtos" in q.criteria[NONE_KEY]


def test_wire_form_has_no_unset_fields():
    q = build_questions([(item(), [cand("1")])], {})["item_0"]
    wire = q.model_dump()
    assert wire["type"] == "choice"
    assert set(wire) == {"type", "instructions", "criteria"}


# --- decide ----------------------------------------------------------------------------------


def test_no_candidates_means_no_match_without_a_call():
    client = FakeClient({})
    [d] = decide([(item(), [])], {}, config(), client)
    assert d.status == "no_match"
    assert d.choice is None and d.confidence is None and d.candidates == []
    assert client.calls == []


def test_batching_and_order_with_empty_items_interleaved():
    names = [f"i{n}" for n in range(7)]
    answers = {n: ("p1", 0.9, {"p1": 0.9, NONE_KEY: 0.1}) for n in names}
    pairs = [(item(n), [] if n == "i2" else [cand("1")]) for n in names]
    client = FakeClient(answers)
    decisions = decide(pairs, {}, config(batch_size=3), client)
    assert [d.item.name for d in decisions] == names
    assert [len(c["questions"]) for c in client.calls] == [3, 3]  # 6 items with candidates
    assert [d.status for d in decisions] == ["accepted"] * 2 + ["no_match"] + ["accepted"] * 4
    assert client.calls[0]["model"] == "jev-1.13"


def test_batch_size_one_makes_one_call_per_item():
    names = ["a", "b", "c"]
    client = FakeClient({n: ("p1", 0.9, {"p1": 0.9, NONE_KEY: 0.1}) for n in names})
    decisions = decide([(item(n), [cand("1")]) for n in names], {}, config(batch_size=1), client)
    assert [len(c["questions"]) for c in client.calls] == [1, 1, 1]
    assert [d.item.name for d in decisions] == names


def answer_for(label, confidence):
    probabilities = {"p1": 0.0, "p2": 0.0, NONE_KEY: 0.0}
    probabilities[label] = confidence
    return {"x": (label, confidence, probabilities)}


@pytest.mark.parametrize(
    ("label", "confidence", "status"),
    [
        ("p1", 0.80, "accepted"),  # exactly accept_at
        ("p1", 0.99, "accepted"),
        ("p1", 0.7999, "ask"),
        ("p1", 0.50, "ask"),
        ("p1", 0.20, "ask"),
        (NONE_KEY, 0.80, "no_match"),
        (NONE_KEY, 0.95, "no_match"),
        (NONE_KEY, 0.79, "ask"),
        (NONE_KEY, 0.1, "ask"),
    ],
)
def test_policy_boundaries(label, confidence, status):
    client = FakeClient(answer_for(label, confidence))
    [d] = decide([(item("x"), [cand("1"), cand("2")])], {}, config(), client)
    assert d.status == status
    assert d.confidence == confidence
    assert d.choice == (None if label == NONE_KEY else label.removeprefix("p"))


def test_nothing_fit_flag_below_ask_below():
    cfg = config()
    results = {}
    for confidence in (0.2, 0.4999, 0.5, 0.7, 0.85):
        client = FakeClient(answer_for("p1", confidence))
        [d] = decide([(item("x"), [cand("1"), cand("2")])], {}, cfg, client)
        results[confidence] = nothing_fit(d, cfg)
    assert results == {0.2: True, 0.4999: True, 0.5: False, 0.7: False, 0.85: False}


def test_nothing_fit_false_for_no_candidates():
    cfg = config()
    [d] = decide([(item(), [])], {}, cfg, FakeClient({}))
    assert not nothing_fit(d, cfg)


def test_probabilities_map_back_to_product_ids():
    client = FakeClient({"x": ("p22", 0.6, {"p11": 0.3, "p22": 0.6, NONE_KEY: 0.1})})
    [d] = decide([(item("x"), [cand("11"), cand("22")])], {}, config(), client)
    assert d.probabilities == {"11": 0.3, "22": 0.6, "nenhum": 0.1}
    assert d.choice == "22"


def test_missing_answer_raises():
    class Empty:
        def system_one(self, **kw):
            return SimpleNamespace(choices={})

    with pytest.raises(DecideError):
        decide([(item("x"), [cand("1")])], {}, config(), Empty())


def test_batch_size_one_runs_in_parallel():
    barrier = threading.Barrier(3, timeout=5)

    class Client(FakeClient):
        def system_one(self, state, questions, model=None):
            barrier.wait()  # only passes if three calls are in flight at once
            return super().system_one(state, questions, model)

    names = ["a", "b", "c"]
    client = Client({n: ("p1", 0.9, {"p1": 0.9, NONE_KEY: 0.1}) for n in names})
    decisions = decide([(item(n), [cand("1")]) for n in names], {}, config(batch_size=1), client)
    assert [d.item.name for d in decisions] == names
    assert len(client.threads) == 3
