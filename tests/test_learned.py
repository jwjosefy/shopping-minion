from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from shopping_minion.config import DecideConfig, HistoryConfig, load_decide_config
from shopping_minion.decide import OFFER_RULE, build_questions, decide, describe_learned
from shopping_minion.history import ItemHistory, ProductHistory
from shopping_minion.items import Candidate, Decision, Item, Quantity
from shopping_minion.learned import LearnedPreference, learned_preferences
from shopping_minion.run import decide_config_row, learned_for
from shopping_minion.storage import Storage

LEARNED_SENTENCE = (
    "Entre os produtos que correspondem, prefira o que você escolheu ou comprou antes."
)
HISTORY_SENTENCE = "Entre os produtos que correspondem, prefira o que já foi comprado antes."


def cand(pid, name="Atum Gomes 170g"):
    return Candidate(
        product_id=pid,
        slug="x",
        name=name,
        brand=None,
        price=Decimal("9.5"),
        list_price=None,
        unit_of_sale="un",
        step_kg=None,
        available=True,
    )


def item(name):
    return Item(source_line=name, name=name, search_term=name)


def decision(name, choice, status="accepted"):
    return Decision(
        item=item(name),
        candidates=[cand("1"), cand("2")],
        choice=choice,
        confidence=0.9,
        probabilities={},
        status=status,
    )


def config(learned=True, history="none"):
    return DecideConfig(
        model="m", batch_size=5, accept_at=0.8, ask_below=0.5, history=history, learned=learned
    )


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "db.sqlite")
    yield db
    db.close()


def add_run(storage, day, decisions, removed=()):
    """A run with the next id, created at midday UTC on 2026-09-<day>."""
    run_id = storage.new_run("l.jpg")
    with storage._conn:
        storage._conn.execute(
            "UPDATE runs SET created_at = ? WHERE id = ?",
            (f"2026-09-{day:02d}T12:00:00+00:00", run_id),
        )
    storage.save_decisions(run_id, decisions)
    for product_id in removed:
        storage.log(run_id, "cart_edit", {"line_id": product_id, "quantity": None, "remove": True})
    return run_id


def fill_runs(storage, up_to):
    """Empty runs, so that the next one gets id `up_to + 1`."""
    while len(storage.list_runs()) < up_to:
        add_run(storage, 1, [])


# --- what counts -----------------------------------------------------------------------------


def test_only_runs_from_learn_from_run_count(storage):
    fill_runs(storage, 7)
    add_run(storage, 2, [decision("atum", "1")])  # run 8: too early
    add_run(storage, 3, [decision("atum", "2")])  # run 9
    found = learned_preferences(storage, 9, exclude_run=None)
    assert found == {
        "atum": {"2": LearnedPreference(product_id="2", times=1, last_at=date(2026, 9, 3))}
    }


def test_accepted_and_user_chosen_count_and_the_rest_do_not(storage):
    add_run(
        storage,
        4,
        [
            decision("atum", "1", "accepted"),
            decision("leite", "1", "user_chosen"),
            decision("sal", None, "skipped"),
            decision("café", "1", "ask"),
            decision("arroz", None, "no_match"),
        ],
    )
    assert set(learned_preferences(storage, 1)) == {"atum", "leite"}


def test_removed_lines_do_not_count(storage):
    add_run(storage, 4, [decision("atum", "1"), decision("leite", "2")], removed=["1"])
    found = learned_preferences(storage, 1)
    assert "atum" not in found
    assert found["leite"]["2"].times == 1


def test_a_quantity_edit_does_not_remove_the_product(storage):
    run_id = add_run(storage, 4, [decision("atum", "1")])
    storage.log(
        run_id,
        "cart_edit",
        {"line_id": "1", "quantity": {"value": 2, "unit": "un"}, "remove": False},
    )
    storage.log(run_id, "reopen", {"index": 0})
    assert learned_preferences(storage, 1)["atum"]["1"].times == 1


def test_the_current_run_is_never_counted(storage):
    add_run(storage, 4, [decision("atum", "1")])
    current = add_run(storage, 5, [decision("atum", "2")])
    found = learned_preferences(storage, 1, exclude_run=current)
    assert list(found["atum"]) == ["1"]


def test_times_and_last_at_across_runs_keyed_by_normalized_name(storage):
    add_run(storage, 2, [decision("Atum", "1")])
    add_run(storage, 9, [decision("ATUM ", "1"), decision("atum", "2")])
    add_run(storage, 5, [decision("atúm", "1")])
    found = learned_preferences(storage, 1)
    assert list(found) == ["atum"]
    assert found["atum"]["1"] == LearnedPreference(
        product_id="1", times=3, last_at=date(2026, 9, 9)
    )
    assert found["atum"]["2"].times == 1


def test_the_same_product_twice_in_a_run_counts_once(storage):
    add_run(storage, 4, [decision("atum", "1"), decision("atum", "1")])
    assert learned_preferences(storage, 1)["atum"]["1"].times == 1


def test_nothing_stored_means_nothing_learned(storage):
    assert learned_preferences(storage, 9) == {}


# --- the question ----------------------------------------------------------------------------


def learned_for_atum(times=3):
    return [{"1": LearnedPreference(product_id="1", times=times, last_at=date(2026, 10, 2))}]


def test_the_fact_on_the_option_and_the_new_sentence():
    pairs = [(item("atum"), [cand("1"), cand("2")])]
    q = build_questions(pairs, {}, learned=learned_for_atum())["item_0"]
    assert q.criteria["p1"].endswith("; escolhido por você 3 vezes, a última em 02/10/2026")
    assert "escolhido" not in q.criteria["p2"]
    assert LEARNED_SENTENCE + " " + OFFER_RULE in q.instructions["question"]
    assert HISTORY_SENTENCE not in q.instructions["question"]


def test_one_time_is_singular():
    q = build_questions([(item("atum"), [cand("1")])], {}, learned=learned_for_atum(1))["item_0"]
    assert q.criteria["p1"].endswith("; escolhido por você 1 vez, a última em 02/10/2026")
    assert describe_learned(learned_for_atum(2)[0]["1"]) == (
        "escolhido por você 2 vezes, a última em 02/10/2026"
    )


def test_both_facts_history_first():
    pairs = [(item("atum"), [cand("1")])]
    history = [
        ItemHistory(
            products={
                "1": ProductHistory(
                    product_id="1",
                    orders=2,
                    last_at=date(2026, 9, 20),
                    last_quantity=Quantity(value=3, unit="un"),
                )
            },
            related=[],
        )
    ]
    q = build_questions(pairs, {}, history, "options", learned_for_atum())["item_0"]
    assert q.criteria["p1"].endswith(
        "comprado antes: 2 vezes, a última em 20/09/2026, 3 un; "
        "escolhido por você 3 vezes, a última em 02/10/2026"
    )
    assert LEARNED_SENTENCE in q.instructions["question"]


def test_history_alone_keeps_its_own_sentence():
    pairs = [(item("atum"), [cand("1")])]
    history = [
        ItemHistory(
            products={
                "1": ProductHistory(
                    product_id="1",
                    orders=2,
                    last_at=date(2026, 9, 20),
                    last_quantity=Quantity(value=3, unit="un"),
                )
            },
            related=[],
        )
    ]
    q = build_questions(pairs, {}, history, "options", [{}])["item_0"]
    assert HISTORY_SENTENCE in q.instructions["question"]
    assert LEARNED_SENTENCE not in q.instructions["question"]


def test_no_entries_or_none_is_byte_identical_to_today():
    pairs = [(item("atum"), [cand("1"), cand("2")]), (item("leite"), [cand("3")])]
    plain = build_questions(pairs, {})
    assert build_questions(pairs, {}, learned=None) == plain
    assert build_questions(pairs, {}, learned=[{}, {}]) == plain
    # an entry for a product that is not a candidate shows nothing either
    other = [{"99": LearnedPreference(product_id="99", times=2, last_at=date(2026, 9, 1))}, {}]
    assert build_questions(pairs, {}, learned=other) == plain


class FakeClient:
    def __init__(self):
        self.questions = []

    def system_one(self, state, questions, model=None):
        self.questions.append(questions)
        choices = {
            key: SimpleNamespace(choice="p1", confidence=0.9, probabilities={"p1": 0.9})
            for key in questions
        }
        return SimpleNamespace(choices=choices)


def test_the_off_switch_sends_the_questions_of_today():
    pairs = [(item("atum"), [cand("1"), cand("2")])]
    off, on = FakeClient(), FakeClient()
    decide(pairs, {}, config(learned=False), off, learned=learned_for_atum())
    decide(pairs, {}, config(learned=True), on, learned=learned_for_atum())
    plain = FakeClient()
    decide(pairs, {}, config(learned=True), plain)
    assert off.questions == plain.questions
    assert off.questions != on.questions
    assert "escolhido por você" in on.questions[0]["item_0"].criteria["p1"]


def test_learned_lines_up_with_items_through_batches_and_skipped_items():
    pairs = [(item("leite"), []), (item("atum"), [cand("1")])]
    client = FakeClient()
    learned = [{}, learned_for_atum()[0]]
    decide(pairs, {}, config(), client, learned=learned)
    assert "escolhido por você" in client.questions[0]["item_0"].criteria["p1"]
    with pytest.raises(ValueError):
        decide(pairs, {}, config(), FakeClient(), learned=[{}])


def test_config_defaults_and_the_decide_row():
    assert DecideConfig(model="m", batch_size=1, accept_at=0.8, ask_below=0.5).learned is False
    assert HistoryConfig(first_sync_orders=10, related_lines=10).learn_from_run == 9
    assert decide_config_row(config(learned=True))["learned"] is True
    assert decide_config_row(config(learned=False))["learned"] is False
    assert load_decide_config().learned is True  # config/decide.yaml


# --- wiring ----------------------------------------------------------------------------------


def test_learned_for_excludes_the_current_run_and_aligns_with_the_items(storage):
    add_run(storage, 4, [decision("atum", "1")])
    current = add_run(storage, 5, [decision("atum", "2"), decision("leite", "2")])
    items = [item("Atum"), item("pão")]
    got = learned_for(storage, items, config(), 1, current)
    assert got == [
        {"1": LearnedPreference(product_id="1", times=1, last_at=date(2026, 9, 4))},
        {},
    ]
    assert learned_for(storage, items, config(learned=False), 1, current) is None
