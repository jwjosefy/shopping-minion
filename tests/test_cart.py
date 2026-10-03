import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from shopping_minion.cart import add_all, add_to_cart, product_url, read_cart_drawer
from shopping_minion.items import Candidate, CartTarget

FIXTURES = Path(__file__).parent / "fixtures" / "search"


def candidate(product_id="1", slug="atum", unit="un", step_kg=None) -> Candidate:
    return Candidate(
        product_id=product_id,
        slug=slug,
        name="Atum",
        brand=None,
        price=Decimal("1"),
        list_price=None,
        unit_of_sale=unit,
        step_kg=step_kg,
        available=True,
    )


def test_product_url():
    url = product_url("6137677", "atum-solido-coqueiro-natural-170g")
    assert url.endswith("/produtos/6137677/atum-solido-coqueiro-natural-170g")


def test_fixture_candidates_have_buildable_urls():
    from shopping_minion.search import candidates_from_response

    body = json.loads((FIXTURES / "atum.json").read_text())
    for c in candidates_from_response(body):
        assert product_url(c.product_id, c.slug).endswith(f"/produtos/{c.product_id}/{c.slug}")


# A made-up product page with the structure site-notes describes. The buy box has the add
# button, which turns into a stepper `[trash] qty [+]`. A "same category" card with its own
# round `+`, a two-button div and its own Peso/Unidade switch (set to Unidade) sits outside
# the buy box and must never be touched or read.
PAGE = """
<style>button {{ min-width: 20px; min-height: 20px }}</style>
<div class="product-renderer-info-box">
  <h5>Produto</h5>
  {switch}
  <div id="ctl">{control}</div>
</div>
<h2>Da mesma categoria</h2>
<div class="item-product-wrapper">
  <div role="radiogroup" aria-label="Seletor de unidade de venda">
    <button role="radio" aria-checked="false">Peso</button>
    <button role="radio" aria-checked="true">Unidade</button></div>
  <div id="other"><button>x</button><span>7</span><button id="otherplus"
    onclick="this.previousElementSibling.textContent='touched'">+</button></div>
</div>
<script>
  const step = {step}, unit = "{unit}";
  let qty = {start};
  const fmt = () => unit === "g" ? Math.round(qty * 1000) + "g" : String(qty);
  function stepper() {{
    document.getElementById("ctl").innerHTML =
      '<button class="t"></button><span id="q">' + fmt() + '</span>' +
      '<button class="p i" onclick="plus()"></button>';
  }}
  // Like the real site when logged in: an UpdateCart mutation shortly after each change.
  const sync = () => {{ if ({sync}) setTimeout(() => fetch("/graphql", {{method: "POST",
    body: JSON.stringify({{operationName: "UpdateCart"}})}}), 300); }};
  function add() {{ qty = step; stepper(); sync(); }}
  function plus() {{
    if ({stuck}) return;
    qty = Math.round((qty + step) * 10) / 10; stepper(); sync();
  }}
</script>
"""
ADD = '<button onclick="add()"><p>Adicionar ao carrinho</p></button>'
STEPPER_ALREADY = (
    '<button class="t"></button><span>2</span><button class="p i" onclick="plus()"></button>'
)
SWITCH = (
    '<div role="radiogroup" aria-label="Seletor de unidade de venda">'
    '<button role="radio" aria-checked="{peso}">Peso</button>'
    '<button role="radio" aria-checked="{unidade}">Unidade</button></div>'
)


def serve(page, control=ADD, step=1, unit="un", start=0, stuck="false", switch="", sync="true"):
    html = PAGE.format(
        control=control, step=step, unit=unit, start=start, stuck=stuck, switch=switch, sync=sync
    )
    page.route("**/graphql", lambda route: route.fulfill(status=200, body="{}"))
    page.route(
        "**/produtos/**",
        lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html),
    )


def test_three_clicks_on_a_unit_product(page):
    serve(page)
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=3))
    assert result.status == "added"
    assert result.quantity_shown == "3"
    assert page.locator("#other span").inner_text() == "7"  # the card outside was not touched


def test_three_clicks_on_a_kg_product_in_peso_mode(page):
    switch = SWITCH.format(peso="true", unidade="false")
    serve(page, step=0.1, unit="g", switch=switch)
    result = add_to_cart(
        page, candidate(unit="kg", step_kg=0.1), CartTarget(product_id="1", clicks=3)
    )
    assert (result.status, result.quantity_shown) == ("added", "300g")


def test_unidade_mode_fails_without_touching_anything(page):
    switch = SWITCH.format(peso="false", unidade="true")
    serve(page, step=0.1, unit="g", switch=switch)
    result = add_to_cart(page, candidate(unit="kg"), CartTarget(product_id="1", clicks=1))
    assert result.status == "failed"
    assert "Peso" in result.message
    assert page.get_by_role("button", name="Adicionar ao carrinho").count() == 1


def test_already_in_cart_is_left_untouched(page):
    serve(page, control=STEPPER_ALREADY)
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=2))
    assert result.status == "untouched"
    assert result.message == "não mexido: já estava no carrinho"
    assert result.quantity_shown == "2"
    assert page.locator("#ctl span").inner_text() == "2"


def test_stepper_that_does_not_change_fails(page, monkeypatch):
    monkeypatch.setattr("shopping_minion.cart.STEPPER_WAIT_SECONDS", 0.5)
    serve(page, stuck="true")
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=3))
    assert result.status == "failed"
    assert result.quantity_shown == "1"
    assert "clique 2" in result.message


def test_candidate_and_target_must_match(page):
    with pytest.raises(ValueError):
        add_to_cart(page, candidate(product_id="1"), CartTarget(product_id="2", clicks=1))


def test_add_all_is_sequential_and_reports_progress(page):
    serve(page)
    seen = []
    pairs = [(candidate(), CartTarget(product_id="1", clicks=1)) for _ in range(2)]
    results = add_all(page, pairs, progress=lambda i, n, c, r: seen.append((i, n, r.status)))
    assert [r.status for r in results] == ["added", "added"]
    assert seen == [(1, 2, "added"), (2, 2, "added")]


def test_unconfirmed_change_fails(page, monkeypatch):
    """The stepper moved but the site never confirmed it (what lost papel higiênico live)."""
    monkeypatch.setattr("shopping_minion.cart.SYNC_WAIT_SECONDS", 1.0)
    serve(page, sync="false")
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=2))
    assert result.status == "failed"
    assert result.message == "o site não confirmou a mudança no carrinho"


def test_anonymous_cart_needs_no_confirmation(page):
    serve(page, sync="false")
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=2), wait_sync=False)
    assert (result.status, result.quantity_shown) == ("added", "2")


def test_read_cart_drawer(page):
    page.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html; charset=utf-8",
            body="""
            <style>button { min-width: 20px; min-height: 20px }</style>
            <header><div data-test="cart-btn"><button
              onclick="document.getElementById('d').hidden=false">0</button></div></header>
            <div id="d" role="dialog" hidden><h2>Carrinho</h2>
              <div><p>Atum Sólido</p><p>R$ 41,94</p>
                <div><button></button><span>3</span><button></button></div></div>
              <div><p>Filé De Peito Frango Resf Kg</p><p>R$ 8,40</p>
                <div><button></button><span>300g</span><button></button></div></div>
            </div>""",
        ),
    )
    page.goto("https://example.invalid/")
    assert read_cart_drawer(page) == [
        ("Atum Sólido", "3"),
        ("Filé De Peito Frango Resf Kg", "300g"),
    ]
    assert page.get_by_role("dialog").is_visible()  # left open


@pytest.mark.live
def test_live_add_to_cart_atum_and_frango():
    """Adds to an anonymous, fresh cart. The lead runs this one; it was not run by T4."""
    from shopping_minion.browser import open_browser
    from shopping_minion.items import Item
    from shopping_minion.search import search

    with open_browser(auth_file=Path("/nonexistent/anonymous.json")) as (_browser, context):
        page = context.new_page()
        atum = search(page, Item(source_line="atum", name="atum", search_term="atum"))[0]
        frango = next(
            c
            for c in search(
                page,
                Item(
                    source_line="file de peito de frango",
                    name="file de peito de frango",
                    search_term="file de peito de frango",
                ),
            )
            if c.name == "Filé De Peito Frango Resf Kg"
        )
        results = add_all(
            page,
            wait_sync=False,
            targets=[
                (atum, CartTarget(product_id=atum.product_id, clicks=3)),
                (frango, CartTarget(product_id=frango.product_id, clicks=3)),
            ],
        )
        assert [(r.status, r.quantity_shown) for r in results] == [
            ("added", "3"),
            ("added", "300g"),
        ]
        lines = read_cart_drawer(page)
        assert [q for _, q in lines] == ["3", "300g"] or sorted(q for _, q in lines) == [
            "3",
            "300g",
        ]


@pytest.mark.live
def test_live_add_to_cart_papel_higienico():
    """The product a live run had trouble with (2026-10-01), on an anonymous fresh cart."""
    from shopping_minion.browser import open_browser
    from shopping_minion.items import Item
    from shopping_minion.search import search

    with open_browser(auth_file=Path("/nonexistent/anonymous.json")) as (_browser, context):
        page = context.new_page()
        item = Item(source_line="papel", name="papel higiênico", search_term="papel higiênico")
        papel = next(
            c
            for c in search(page, item)
            if c.name.startswith("Papel Higiênico Fancy Folha Dupla 30m")
        )
        [result] = add_all(
            page, [(papel, CartTarget(product_id=papel.product_id, clicks=2))], wait_sync=False
        )
        assert (result.status, result.quantity_shown) == ("added", "2"), result.message
        assert [q for _, q in read_cart_drawer(page)] == ["2"]


def test_add_all_stops_before_the_next_product(page):
    serve(page)
    pairs = [(candidate(), CartTarget(product_id="1", clicks=1))] * 3
    results = add_all(page, pairs, should_stop=lambda: True, wait_sync=False)
    assert results == []


def test_quantity_text_above_one_kg_uses_a_dot():
    # Seen live on 2026-10-02: the stepper and the drawer show "1.5kg" above 1 kg. Not
    # matching it made every click past 1 kg time out as "a quantidade não mudou".
    from shopping_minion.cart import QUANTITY_TEXT

    for text in ("1.1kg", "1.5kg", "1.2 kg", "900g", "1kg", "3", "1,5kg"):
        assert QUANTITY_TEXT.match(text), text
    for text in ("1 2", "Instruções Remover", "1.5.2kg"):
        assert not QUANTITY_TEXT.match(text), text


DEAD_ADD = "<button><p>Adicionar ao carrinho</p></button>"  # the click does nothing (runs 10, 11)


def test_an_add_the_site_does_not_take_fails_and_leaves_a_screenshot(page, monkeypatch, tmp_path):
    monkeypatch.setattr("shopping_minion.cart.ADD_WAIT_SECONDS", 0.5)
    monkeypatch.setattr("shopping_minion.cart.LOG_DIR", tmp_path / "logs")
    serve(page, control=DEAD_ADD)
    result = add_to_cart(page, candidate(product_id="42"), CartTarget(product_id="42", clicks=2))
    assert result.status == "failed"
    assert result.message == "o site não aceitou o clique em Adicionar"
    assert page.get_by_role("button", name="Adicionar ao carrinho").count() == 1  # nothing else
    assert page.locator("#other span").inner_text() == "7"  # the other card was not touched
    shots = list((tmp_path / "logs").glob("add-42-*.png"))
    assert len(shots) == 1
    assert re.fullmatch(r"add-42-\d{8}-\d{6}\.png", shots[0].name)
    assert shots[0].read_bytes().startswith(b"\x89PNG")


def test_a_failing_screenshot_does_not_break_the_run(page, monkeypatch, tmp_path):
    monkeypatch.setattr("shopping_minion.cart.ADD_WAIT_SECONDS", 0.5)
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the directory should be")
    monkeypatch.setattr("shopping_minion.cart.LOG_DIR", blocker)
    serve(page, control=DEAD_ADD)
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=1))
    assert result.message == "o site não aceitou o clique em Adicionar"


def test_a_slow_add_that_does_show_the_stepper_is_not_the_not_taken_case(
    page, monkeypatch, tmp_path
):
    monkeypatch.setattr("shopping_minion.cart.LOG_DIR", tmp_path / "logs")
    serve(page)
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=1))
    assert result.status == "added"
    assert not (tmp_path / "logs").exists()
