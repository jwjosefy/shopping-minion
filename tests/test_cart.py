import json
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
# round `+` and a two-button div sits outside the buy box and must never be touched.
PAGE = """
<style>button {{ min-width: 20px; min-height: 20px }}</style>
<div class="product-renderer-info-box">
  <h5>Produto</h5>
  <div id="ctl">{control}</div>
</div>
{switch}
<h2>Da mesma categoria</h2>
<div class="item-product-wrapper">
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
  function add() {{ qty = step; stepper(); }}
  function plus() {{ if ({stuck}) return; qty = Math.round((qty + step) * 10) / 10; stepper(); }}
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


def serve(page, control=ADD, step=1, unit="un", start=0, stuck="false", switch=""):
    html = PAGE.format(
        control=control, step=step, unit=unit, start=start, stuck=stuck, switch=switch
    )
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


def test_already_in_cart_fails_and_leaves_it_alone(page):
    serve(page, control=STEPPER_ALREADY)
    result = add_to_cart(page, candidate(), CartTarget(product_id="1", clicks=2))
    assert result.status == "failed"
    assert result.message == "já está no carrinho"
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
            [
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
