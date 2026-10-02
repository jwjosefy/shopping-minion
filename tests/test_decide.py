import threading
from datetime import UTC, date, datetime
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
    format_date,
    format_quantity,
    format_times,
    nothing_fit,
)
from shopping_minion.history import ItemHistory, ProductHistory
from shopping_minion.items import Candidate, Item, Quantity
from shopping_minion.orders import OrderLine


def cand(
    pid,
    name="Atum Gomes de Sá 170g",
    brand="Gomes",
    price="9.5",
    unit="un",
    available=True,
    list_price=None,
):
    return Candidate(
        product_id=pid,
        slug="x",
        name=name,
        brand=brand,
        price=Decimal(price) if price is not None else None,
        list_price=Decimal(list_price) if list_price is not None else None,
        unit_of_sale=unit,
        step_kg=0.3 if unit == "kg" else None,
        available=available,
    )


def item(name="atum", **kw):
    return Item(source_line=kw.pop("source_line", name), name=name, search_term=name, **kw)


def config(batch_size=5, accept_at=0.8, ask_below=0.5, history="none"):
    return DecideConfig(
        model="jev-1.13",
        batch_size=batch_size,
        accept_at=accept_at,
        ask_below=ask_below,
        history=history,
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


def test_candidate_description_with_offer():
    on_sale = cand("1", name="Laranja Pêra Rio Kg", brand=None, price="5.98", list_price="6.49")
    assert describe_candidate(on_sale) == (
        "Laranja Pêra Rio Kg, em oferta, de R$ 6,49 por R$ 5,98, vendido por unidade"
    )
    kg = cand("2", name="Laranja", brand=None, price="5.98", unit="kg", list_price="6.49")
    assert describe_candidate(kg) == "Laranja, em oferta, de R$ 6,49 por R$ 5,98, vendido por kg"


def test_no_offer_when_list_price_is_not_above_price():
    same = cand("1", brand=None, price="9.5", list_price="9.5")
    assert describe_candidate(same) == "Atum Gomes de Sá 170g, R$ 9,50, vendido por unidade"
    below = cand("2", brand=None, price="9.5", list_price="8")
    assert "em oferta" not in describe_candidate(below)


def test_both_questions_prefer_the_offer_before_nenhum():
    sentence = "Entre produtos equivalentes, prefira o que está em oferta."
    plain = build_questions([(item("atum"), [cand("1")])], {})["item_0"]
    with_pref = build_questions(
        [(item("feijão"), [cand("1")])], {"feijão": {"variante": "carioca"}}
    )["item_0"]
    for q in (plain, with_pref):
        text = q.instructions["question"]
        assert sentence in text
        assert text.index(sentence) < text.index("nenhum")


def test_nenhum_criterion_says_when_it_is_right():
    q = build_questions([(item(), [cand("1")])], {})["item_0"]
    assert "Nenhum dos produtos" in q.criteria[NONE_KEY]


def test_wire_form_has_no_unset_fields():
    q = build_questions([(item(), [cand("1")])], {})["item_0"]
    wire = q.model_dump()
    assert wire["type"] == "choice"
    assert set(wire) == {"type", "instructions", "criteria"}


# --- history in the question (LLD-M4 section 11.2) -------------------------------------------

BASE_QUESTION = (
    "Qual produto da loja corresponde ao `item` da lista de compras? "
    "Respeite as restrições de `item`. "
    "Entre produtos equivalentes, prefira o que está em oferta. "
    "Se nenhum produto corresponde, escolha `nenhum`."
)
A_SENTENCE = "Entre os produtos que correspondem, prefira o que já foi comprado antes."
B_SENTENCE = (
    "Considere o `historico` de compras: entre os produtos que correspondem, "
    "prefira um que já foi comprado antes."
)


def product_history(pid="1", orders=3, day=20, value=2.045, unit="kg"):
    return ProductHistory(
        product_id=pid,
        orders=orders,
        last_at=date(2026, 9, day),
        last_quantity=Quantity(value=value, unit=unit),
    )


def order_line(pid, name, quantity=1.0, unit="un", day=5):
    return OrderLine(
        order_id="o9",
        placed_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
        product_id=pid,
        name=name,
        quantity=quantity,
        unit=unit,
        total_price=None,
    )


LARANJA = [
    cand("1", name="Laranja Pêra Rio Kg", brand=None, price="5.98", unit="kg", list_price="6.49"),
    cand("2", name="Laranja Bahia Kg", brand=None, price="8.99", unit="kg"),
]


def test_none_and_missing_histories_leave_the_question_as_it_was():
    pairs = [(item("laranja"), LARANJA)]
    history = [ItemHistory(products={"1": product_history()}, related=[order_line("9", "x")])]
    plain = build_questions(pairs, {})["item_0"]
    assert plain.instructions == {
        "question": BASE_QUESTION,
        "item": {"name": "laranja", "source_line": "laranja"},
    }
    assert plain.criteria["p1"] == (
        "Laranja Pêra Rio Kg, em oferta, de R$ 6,49 por R$ 5,98, vendido por kg"
    )
    assert build_questions(pairs, {}, history, "none")["item_0"] == plain
    assert build_questions(pairs, {}, None, "options")["item_0"] == plain
    assert build_questions(pairs, {}, None, "list")["item_0"] == plain


def test_variant_a_annotates_only_the_options_with_history():
    pairs = [(item("laranja"), LARANJA)]
    history = [ItemHistory(products={"1": product_history()}, related=[])]
    q = build_questions(pairs, {}, history, "options")["item_0"]
    assert q.criteria["p1"] == (
        "Laranja Pêra Rio Kg, em oferta, de R$ 6,49 por R$ 5,98, vendido por kg; "
        "comprado antes: 3 vezes, a última em 20/09/2026, 2,045 kg"
    )
    assert q.criteria["p2"] == "Laranja Bahia Kg, R$ 8,99, vendido por kg"
    assert q.instructions["question"] == BASE_QUESTION.replace(
        "Entre produtos", f"{A_SENTENCE} Entre produtos"
    )
    assert "historico" not in q.instructions


def test_variant_a_with_a_preference_and_a_single_purchase_in_units():
    prefs = {"atum": {"marca": "Gomes"}}
    history = [
        ItemHistory(products={"1": product_history(orders=1, value=3, unit="un")}, related=[])
    ]
    q = build_questions([(item("atum"), [cand("1", brand=None)])], prefs, history, "options")
    q = q["item_0"]
    assert q.criteria["p1"].endswith("; comprado antes: 1 vez, a última em 20/09/2026, 3 un")
    text = q.instructions["question"]
    assert A_SENTENCE in text
    assert "`preferencia`" in text
    assert text.index(A_SENTENCE) < text.index("em oferta") < text.index("nenhum")


def test_variant_a_does_nothing_for_an_item_without_products():
    related_only = ItemHistory(products={}, related=[order_line("9", "Suco Laranja 1L")])
    for history in (ItemHistory(products={}, related=[]), related_only):
        q = build_questions([(item("laranja"), LARANJA)], {}, [history], "options")["item_0"]
        assert q.instructions["question"] == BASE_QUESTION
        assert q.criteria["p1"] == describe_candidate(LARANJA[0])


def test_variant_b_lists_the_products_then_the_related_lines():
    history = [
        ItemHistory(
            products={"2": product_history("2", orders=1, day=3, value=0.5)},
            related=[order_line("9", "Suco Xando Laranja 1L", 2, "un", day=5)],
        )
    ]
    q = build_questions([(item("laranja"), LARANJA)], {}, history, "list")["item_0"]
    assert q.instructions["historico"] == [
        "Laranja Bahia Kg | 0,5 kg | 03/09/2026",
        "Suco Xando Laranja 1L | 2 un | 05/09/2026",
    ]
    assert q.instructions["question"] == BASE_QUESTION.replace(
        "Entre produtos", f"{B_SENTENCE} Entre produtos"
    )
    assert q.criteria["p1"] == describe_candidate(LARANJA[0])  # the options stay as today


def test_variant_b_needs_products_or_related_lines():
    q = build_questions(
        [(item("laranja"), LARANJA)], {}, [ItemHistory(products={}, related=[])], "list"
    )
    q = q["item_0"]
    assert q.instructions == {
        "question": BASE_QUESTION,
        "item": {"name": "laranja", "source_line": "laranja"},
    }
    only_related = ItemHistory(products={}, related=[order_line("9", "Leite Ninho 1L")])
    q = build_questions([(item("leite"), [cand("1")])], {}, [only_related], "list")["item_0"]
    assert q.instructions["historico"] == ["Leite Ninho 1L | 1 un | 05/09/2026"]


def test_histories_line_up_with_the_items_even_with_empty_ones_between():
    pairs = [(item("laranja"), LARANJA), (item("vazio"), []), (item("atum"), [cand("7")])]
    histories = [
        ItemHistory(products={"1": product_history()}, related=[]),
        ItemHistory(products={}, related=[]),
        ItemHistory(products={"7": product_history("7", orders=2, value=2, unit="un")}, related=[]),
    ]
    client = FakeClient({"laranja": ("p1", 0.9, {"p1": 0.9}), "atum": ("p7", 0.9, {"p7": 0.9})})
    decide(pairs, {}, config(history="options"), client, histories)
    (call,) = client.calls
    assert "3 vezes" in call["questions"]["item_0"].criteria["p1"]
    assert "2 vezes" in call["questions"]["item_1"].criteria["p7"]


def test_decide_without_histories_sends_the_wave_one_questions():
    client = FakeClient({"laranja": ("p1", 0.9, {"p1": 0.9})})
    decide([(item("laranja"), LARANJA)], {}, config(history="options"), client)
    assert client.calls[0]["questions"]["item_0"].instructions["question"] == BASE_QUESTION


def test_decide_rejects_histories_of_the_wrong_length():
    with pytest.raises(ValueError):
        decide([(item("laranja"), LARANJA)], {}, config(), FakeClient({}), [])


def test_the_default_history_mode_is_none():
    cfg = DecideConfig(model="m", batch_size=1, accept_at=0.8, ask_below=0.5)
    assert cfg.history == "none"


def test_formatting_helpers():
    assert format_times(1) == "1 vez"
    assert format_times(3) == "3 vezes"
    assert format_date(date(2026, 9, 5)) == "05/09/2026"
    assert format_quantity(Quantity(value=2.045, unit="kg")) == "2,045 kg"
    assert format_quantity(Quantity(value=0.5, unit="kg")) == "0,5 kg"
    assert format_quantity(Quantity(value=3, unit="un")) == "3 un"
    assert format_quantity(Quantity(value=3.0, unit="kg")) == "3 kg"
    assert format_quantity(Quantity(value=10.0, unit="un")) == "10 un"
    assert format_quantity(Quantity(value=12, unit="kg")) == "12 kg"


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
