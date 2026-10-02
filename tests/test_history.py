from datetime import UTC, datetime
from decimal import Decimal

from shopping_minion.history import (
    lines_for_item,
    near_misses,
    norm,
    product_histories,
    words,
)
from shopping_minion.items import Candidate, Item, Quantity
from shopping_minion.orders import OrderLine


def line(order_id, day, product_id, name, quantity=1.0, unit="un"):
    return OrderLine(
        order_id=order_id,
        placed_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),  # midday: the same date anywhere
        product_id=product_id,
        name=name,
        quantity=quantity,
        unit=unit,
        total_price=Decimal("10.00"),
    )


def cand(pid, name):
    return Candidate(
        product_id=pid,
        slug="x",
        name=name,
        brand=None,
        price=Decimal("5.00"),
        list_price=None,
        unit_of_sale="kg",
        step_kg=0.5,
        available=True,
    )


def item(name, search_term=None):
    return Item(
        source_line=name, name=name, search_term=name if search_term is None else search_term
    )


# --- norm and words --------------------------------------------------------------------------


def test_norm_strips_accents_case_and_punctuation():
    assert norm("Laranja Pêra Rio Kg") == "laranja pera rio kg"
    assert norm("laranja pêra rio kg") == norm("Laranja Pêra Rio Kg")
    assert norm("  Leite, Ninho (Integral) 1L! ") == "leite ninho integral 1l"


def test_words_drop_stopwords_but_keep_numbers_and_units():
    assert words("Pão de Queijo com Orégano e Alho") == {"pao", "queijo", "oregano", "alho"}
    assert words("Refrigerante 2L") != words("Refrigerante 600ml")
    assert {"2l"} <= words("Refrigerante 2L")


# --- step 1 ----------------------------------------------------------------------------------


def test_product_histories_count_orders_and_take_the_newest():
    lines = [
        line("o1", 1, "10", "Laranja Pêra Rio Kg", 1.5, "kg"),
        line("o3", 20, "10", "Laranja Pêra Rio Kg", 2.045, "kg"),
        line("o2", 10, "10", "Laranja Pêra Rio Kg", 3.0, "kg"),
        line("o2", 10, "10", "Laranja Pêra Rio Kg", 1.0, "kg"),  # same order twice: counts once
    ]
    history = product_histories(lines)["10"]
    assert history.orders == 3
    assert (history.last_at.year, history.last_at.month, history.last_at.day) == (2026, 9, 20)
    assert history.last_quantity == Quantity(value=2.045, unit="kg")


def test_the_laranja_case_matches_by_id_and_ignores_the_rest():
    candidates = [
        cand("10", "Laranja Pêra Rio Kg"),
        cand("11", "Laranja Bahia Kg"),
        cand("12", "Pão De Laranja Kg"),
    ]
    lines = [
        line("o1", 1, "10", "Laranja Pêra Rio Kg", 1.0, "kg"),
        line("o2", 20, "10", "Laranja Pêra Rio Kg", 2.045, "kg"),
        line("o3", 12, "10", "Laranja Pêra Rio Kg", 1.2, "kg"),
        line("o3", 12, "99", "Leite Ninho Integral 1L"),
    ]
    history = lines_for_item(item("laranja"), candidates, lines, k=5)
    assert list(history.products) == ["10"]  # candidates without lines get nothing
    assert history.products["10"].orders == 3
    assert history.related == []


def test_a_same_name_line_with_another_id_is_not_step_one():
    # step 1 is by id only: a renamed or re-listed product doesn't annotate a candidate
    candidates = [cand("10", "Laranja Pêra Rio Kg")]
    lines = [line("o1", 1, "77", "Laranja Pêra Rio Kg", 1.0, "kg")]
    history = lines_for_item(item("laranja"), candidates, lines, k=5)
    assert history.products == {}
    assert [related.product_id for related in history.related] == ["77"]


# --- step 2 ----------------------------------------------------------------------------------


def test_step_two_finds_the_item_outside_the_candidates():
    lines = [line("o1", 1, "50", "Leite Ninho Integral 1L"), line("o1", 1, "51", "Pão Francês")]
    history = lines_for_item(item("leite"), [cand("1", "Leite Italac 1L")], lines, k=5)
    assert [r.product_id for r in history.related] == ["50"]
    assert history.products == {}


def test_step_two_keeps_the_noise_it_cannot_tell_apart():
    # "laranja" also matches a juice; the question is what B shows, so this is by design
    lines = [line("o1", 1, "60", "Suco Xando Laranja 1L")]
    history = lines_for_item(item("laranja"), [cand("1", "Laranja Pêra Rio Kg")], lines, k=5)
    assert [r.name for r in history.related] == ["Suco Xando Laranja 1L"]


def test_step_two_needs_every_word_of_the_search_term():
    lines = [line("o1", 1, "50", "Leite Ninho Integral 1L"), line("o1", 1, "51", "Leite Desnatado")]
    history = lines_for_item(item("x", "leite integral"), [], lines, k=5)
    assert [r.product_id for r in history.related] == ["50"]


def test_step_two_is_newest_first_one_per_product_and_capped_at_k():
    lines = [
        line("o1", 1, "50", "Leite A"),
        line("o2", 20, "50", "Leite A"),
        line("o3", 15, "51", "Leite B"),
        line("o4", 10, "52", "Leite C"),
        line("o5", 5, "53", "Leite D"),
    ]
    history = lines_for_item(item("leite"), [], lines, k=3)
    assert [(r.product_id, r.placed_at.day) for r in history.related] == [
        ("50", 20),
        ("51", 15),
        ("52", 10),
    ]


def test_step_two_ignores_lines_a_candidate_matched():
    lines = [line("o1", 1, "1", "Leite Italac 1L")]
    history = lines_for_item(item("leite"), [cand("1", "Leite Italac 1L")], lines, k=5)
    assert history.related == []
    assert "1" in history.products


def test_an_empty_search_term_matches_nothing_in_step_two():
    lines = [line("o1", 1, "50", "Leite Ninho")]
    assert lines_for_item(item("de", "de da"), [], lines, k=5).related == []
    assert lines_for_item(item("x", ""), [], lines, k=5).related == []


# --- step 3 ----------------------------------------------------------------------------------


def test_near_misses_are_related_lines_that_look_like_a_candidate():
    candidates = [cand("1", "Laranja Pêra Rio Kg"), cand("2", "Suco de Uva 1L")]
    lines = [
        line("o1", 1, "60", "Laranja Pera Kg"),  # 3 of 4 words: 0.75
        line("o1", 1, "61", "Suco Xando Laranja Kg"),  # shares laranja and kg: 2 of 6
    ]
    history = lines_for_item(item("laranja"), [], lines, k=5)
    found = near_misses(history, candidates)
    assert [(related.product_id, c.product_id) for related, c, _ in found] == [("60", "1")]
    assert found[0][2] == 0.75


def test_near_misses_threshold_is_a_parameter():
    candidates = [cand("1", "Laranja Pêra Rio Kg")]
    history = lines_for_item(item("laranja"), [], [line("o1", 1, "61", "Suco Xando Laranja Kg")], 5)
    assert near_misses(history, candidates) == []
    assert len(near_misses(history, candidates, threshold=0.3)) == 1
