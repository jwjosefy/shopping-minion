"""Drive our own web app (static/) with Playwright against the stub backend (LLD-M2 section 8).

Only 127.0.0.1 is touched; Vue itself loads from the CDN, the one external request. The store
is never contacted. Run with: uv run pytest -m live tests/test_ui.py
"""

import re
import time
import urllib.request

import pytest
from playwright.sync_api import expect, sync_playwright
from ui_stub import NO_RESULTS_PICK, StubServer

pytestmark = pytest.mark.live

VUE_URL = "https://cdn.jsdelivr.net/npm/vue@3.5.43/dist/vue.global.prod.js"
PHOTO = {"name": "lista.png", "mimeType": "image/png", "buffer": b"\x89PNG\r\n\x1a\n"}
VIEWPORTS = {"phone": (390, 844), "desktop": (1280, 800)}


@pytest.fixture(scope="module", autouse=True)
def vue_reachable():
    try:
        urllib.request.urlopen(urllib.request.Request(VUE_URL, method="HEAD"), timeout=10)
    except OSError:
        pytest.skip("the Vue CDN is not reachable")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


def open_page(browser, server, size):
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    page = context.new_page()
    page.set_default_timeout(8000)
    page.goto(server.url)
    return context, page


def wait_for(predicate, what, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def assert_fits(page, screen):
    width, inner = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert width <= inner, f"{screen}: scrollWidth {width} > innerWidth {inner}"


def screen(page, name):
    loc = page.locator(f'[data-screen="{name}"]')
    expect(loc).to_be_visible()
    return loc


@pytest.mark.parametrize("size", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_whole_flow(browser, size):
    hold = {"reading_list", "syncing_history", "searching", "deciding", "filling_cart"}
    with StubServer(hold=hold) as server:
        stub = server.stub
        context, page = open_page(browser, server, size)
        try:
            # 1. Enviar lista
            idle = screen(page, "idle")
            expect(idle.get_by_text("Enviar lista")).to_be_visible()
            expect(page.locator("[data-test=access]")).to_contain_text("Abra no celular")
            expect(page.locator("[data-test=access-url]")).to_contain_text("stubtoken")
            expect(idle.get_by_text("#7")).to_be_visible()
            assert_fits(page, "idle")
            photo = page.locator("input[data-test=photo]")
            assert photo.get_attribute("accept") == "image/*"
            assert photo.get_attribute("capture") == "environment"
            photo.set_input_files(PHOTO)
            wait_for(lambda: stub.uploads == ["lista.png"], "the upload")

            # 2. Lendo a lista
            reading = screen(page, "reading_list")
            expect(reading).to_contain_text("Lendo a lista")
            expect(page.locator("[data-test=elapsed]")).to_contain_text("s")
            assert_fits(page, "reading_list")
            server.stub.release("reading_list")

            # 3. Revisar lista
            review = screen(page, "reviewing_list")
            expect(review.get_by_text("Revisar lista")).to_be_visible()
            rows = page.locator("[data-test=list-row]")
            expect(rows).to_have_count(3)
            expect(page.locator("tr.review")).to_have_count(1)
            assert_fits(page, "reviewing_list")
            photo_box = page.locator("[data-test=list-photo]").bounding_box()
            table_box = page.locator("table.grid").bounding_box()
            if size[0] < 900:  # on a phone the photo sits above the table
                assert photo_box["y"] + photo_box["height"] <= table_box["y"] + 1
            else:  # beside it on a desktop
                assert photo_box["x"] + photo_box["width"] <= table_box["x"] + 1
            # constraints are comma text; edits are saved without pressing anything
            expect(rows.nth(2).get_by_label("restrições")).to_have_value("cremoso, sem lactose")
            rows.nth(0).get_by_label("nome").fill("leite integral UHT")
            wait_for(lambda: len(stub.list_puts) >= 1, "the debounced save")
            saved = stub.list_puts[-1]["items"]
            assert saved[0]["name"] == "leite integral UHT"
            assert saved[2]["constraints"] == ["cremoso", "sem lactose"]
            assert saved[0]["quantity"] == {"value": 1, "unit": "l"}
            page.locator("[data-test=add-row]").click()
            expect(rows).to_have_count(4)
            rows.nth(3).get_by_role("button", name="remover linha").click()
            expect(rows).to_have_count(3)
            page.locator("[data-test=confirm-list]").click()

            # 4. Lendo os pedidos, Buscando, then Jev
            screen(page, "searching")
            expect(page.locator("[data-test=syncing-history]")).to_have_text("Lendo seus pedidos…")
            assert_fits(page, "syncing_history")
            stub.release("syncing_history")
            searching = screen(page, "searching")
            expect(page.locator("[data-test=search-count]")).to_have_text("3/3")
            expect(searching).to_contain_text("requeijão cremoso")
            assert_fits(page, "searching")
            stub.release("searching")
            expect(page.locator("[data-test=deciding]")).to_have_text("Jev está decidindo…")
            assert_fits(page, "deciding")
            stub.release("deciding")

            # 5. Escolher produto: Jev's pick is first and selected; Enter takes it
            screen(page, "picking")
            expect(page.locator("[data-test=pick-item]")).to_have_text("tomate")
            expect(page.locator("[data-test=pick-count]")).to_have_text("item 1 de 2")
            expect(page.locator("[data-test=jev-note]")).to_contain_text("0,56")
            cards = page.locator(".cand")
            expect(cards).to_have_count(3)
            expect(cards.nth(0)).to_have_attribute("data-test", "cand-t-jev")
            expect(cards.nth(0)).to_have_class(re.compile(r"\bselected\b"))
            expect(cards.nth(0)).to_contain_text("R$")
            expect(cards.nth(0).locator("s")).to_contain_text("9,00")
            expect(cards.nth(0)).to_contain_text("por kg")
            expect(cards.nth(1)).to_contain_text("por kg")
            expect(cards.nth(2)).to_contain_text("por unidade")
            expect(cards.nth(2)).to_contain_text("indisponível")
            assert page.locator(".cand img").first.get_attribute("src").startswith("/stub-img/")
            assert_fits(page, "picking")
            page.keyboard.press("ArrowRight")
            expect(cards.nth(1)).to_have_class(re.compile(r"\bselected\b"))
            page.keyboard.press("ArrowLeft")
            expect(cards.nth(0)).to_have_class(re.compile(r"\bselected\b"))
            page.keyboard.press("Enter")
            wait_for(lambda: len(stub.picks_posted) == 1, "the first pick")
            assert stub.picks_posted[0] == {"index": 0, "product_id": "t-jev"}

            expect(page.locator("[data-test=pick-item]")).to_have_text("requeijão")
            expect(page.locator("[data-test=pick-count]")).to_have_text("item 2 de 2")
            expect(page.locator("[data-test=jev-note]")).to_contain_text("nada parece servir")
            expect(page.locator(".cand.selected")).to_have_count(0)
            expect(page.locator(".chip")).to_have_count(2)
            assert_fits(page, "picking (nothing fits)")
            page.keyboard.press("Enter")  # nothing selected: nothing is taken
            page.keyboard.press("0")
            wait_for(lambda: len(stub.picks_posted) == 2, "the skip")
            assert stub.picks_posted[1] == {"index": 1, "product_id": None}

            # 6. Revisar carrinho
            cart = screen(page, "reviewing_cart")
            lines = page.locator("[data-test=cart-line]")
            expect(lines).to_have_count(3)
            expect(cart).to_contain_text("quantidade assumida")
            expect(cart).to_contain_text("aproximada")
            expect(cart).to_contain_text("quantidade da última compra")
            expect(cart).to_contain_text("2 linhas da lista")
            expect(page.locator("[data-test=skipped]")).to_contain_text("requeijão")
            expect(page.locator("[data-test=total]")).to_contain_text("R$")
            expect(cart).to_contain_text("estimado")
            assert_fits(page, "reviewing_cart")
            clicks = page.locator("[data-test=clicks-p-tomate]")
            expect(clicks).to_have_text("2")  # 1 kg in steps of 0.5 kg
            total_before = page.locator("[data-test=total]").inner_text()
            qty = page.locator("[data-test=qty-p-tomate]")
            qty.fill("2")
            qty.blur()
            expect(clicks).to_have_text("4")
            assert stub.cart_puts[-1] == {
                "lines": [
                    {"line_id": "p-tomate", "quantity": {"value": 2, "unit": "kg"}, "remove": False}
                ]
            }
            assert page.locator("[data-test=total]").inner_text() != total_before
            page.locator("[data-test=remove-p-leite]").click()
            expect(lines).to_have_count(2)
            assert stub.cart_puts[-1]["lines"] == [
                {"line_id": "p-leite", "quantity": None, "remove": True}
            ]
            page.locator("[data-test=confirm-cart]").click()

            # 7. Adicionando
            filling = screen(page, "filling_cart")
            expect(filling).to_contain_text("Adicionando")
            expect(page.locator("[data-test=fill-count]")).to_have_text("1/2")
            expect(filling).to_contain_text("adicionado")
            assert_fits(page, "filling_cart")
            stub.release("filling_cart")

            # 8. Pronto
            done = screen(page, "done")
            expect(done.get_by_role("heading", name="Pronto")).to_be_visible()
            problems = page.locator("[data-test=problem]")
            expect(problems).to_have_count(2)
            expect(page.locator("[data-test=problems]")).to_contain_text(
                "o site não aceitou o clique em Adicionar"
            )
            expect(page.locator("[data-test=problems]")).to_contain_text("QUANTIDADE DIFERENTE")
            expect(page.locator("[data-test=retry]")).to_have_text("tentar de novo (2)")
            expect(page.locator("[data-test=extras]")).to_contain_text("Sabão em pó")
            expect(page.locator("[data-test=extras]")).to_contain_text("não são desta lista")
            assert page.locator("[data-test=oks]").get_attribute("open") is None  # collapsed
            expect(page.locator("[data-test=open-note]")).to_have_text(
                "o carrinho está aberto na janela do navegador; revise e finalize no site"
            )
            assert_fits(page, "done")
            page.locator("[data-test=oks] summary").click()
            assert_fits(page, "done (ok rows open)")
            page.locator("[data-test=new-list]").click()
            screen(page, "idle")
        finally:
            context.close()


def test_cancel_while_reviewing(browser):
    with StubServer(hold={"reading_list"}) as server:
        context, page = open_page(browser, server, VIEWPORTS["desktop"])
        try:
            screen(page, "idle")
            page.locator("input[data-test=photo]").set_input_files(PHOTO)
            screen(page, "reading_list")
            server.stub.release("reading_list")
            screen(page, "reviewing_list")
            page.locator("[data-test=cancel]").click()
            done = screen(page, "done")
            expect(done.get_by_role("heading", name="Cancelado")).to_be_visible()
            expect(page.locator("[data-test=run-message]")).to_have_text("Rodada cancelada.")
            expect(page.locator("[data-test=open-note]")).to_have_count(0)
            page.locator("[data-test=close-browser]").click()
            screen(page, "idle")
        finally:
            context.close()


@pytest.mark.parametrize("size", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_done_lists_every_failure_and_retries_them(browser, size):
    with StubServer(hold={"retry"}) as server:
        stub = server.stub
        stub.go("done")
        context, page = open_page(browser, server, size)
        try:
            done = screen(page, "done")
            problems = page.locator("[data-test=problem]")
            expect(problems).to_have_count(2)
            # product, expected, what the cart shows, and the message, for every line
            tomate, requeijao = problems.nth(0), problems.nth(1)
            expect(tomate).to_contain_text("Tomate italiano kg")
            expect(tomate).to_contain_text("esperado 1kg; no carrinho nada")
            expect(tomate).to_contain_text("o site não aceitou o clique em Adicionar")
            expect(requeijao).to_contain_text("Requeijão cremoso 200g")
            expect(requeijao).to_contain_text("esperado 2; no carrinho 1")
            expect(requeijao).to_contain_text("QUANTIDADE DIFERENTE")
            assert_fits(page, "done with failures")

            page.locator("[data-test=retry]").click()
            filling = screen(page, "filling_cart")
            expect(filling).to_contain_text("Adicionando")
            assert stub.retries == 1
            stub.release("retry")

            done = screen(page, "done")
            expect(page.locator("[data-test=summary]")).to_contain_text("2 de 2")
            expect(page.locator("[data-test=problems]")).to_have_count(0)
            expect(page.locator("[data-test=retry]")).to_have_count(0)
            expect(done.get_by_role("heading", name="Pronto")).to_be_visible()
        finally:
            context.close()


def test_an_item_with_no_results_offers_only_to_skip(browser):
    with StubServer(picks=[NO_RESULTS_PICK]) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            page.locator("input[data-test=photo]").set_input_files(PHOTO)
            screen(page, "reviewing_list")
            page.locator("[data-test=confirm-list]").click()
            screen(page, "picking")
            expect(page.locator("[data-test=pick-item]")).to_have_text("sal grosso")
            expect(page.locator("[data-test=no-results]")).to_have_text(
                "nada encontrado para “sal grosso”"
            )
            expect(page.locator(".cand")).to_have_count(0)
            expect(page.locator("[data-test=take]")).to_have_count(0)
            assert_fits(page, "picking (no results)")
            page.locator("[data-test=skip]").click()
            wait_for(lambda: stub.picks_posted == [{"index": 0, "product_id": None}], "the skip")
            screen(page, "reviewing_cart")
        finally:
            context.close()


def test_resync_on_409_and_reload_resumes(browser):
    with StubServer(hold={"reading_list"}) as server:
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            page.locator("input[data-test=photo]").set_input_files(PHOTO)
            screen(page, "reading_list")
            server.stub.release("reading_list")
            screen(page, "reviewing_list")
            # the run moves on behind the page's back; the next call answers 409 and resyncs
            server.stub.go("reviewing_cart")
            page.locator("[data-test=confirm-list]").click()
            screen(page, "reviewing_cart")
            page.reload()
            screen(page, "reviewing_cart")
            assert_fits(page, "reviewing_cart after reload")
        finally:
            context.close()


def test_access_block_hidden_when_it_errors(browser):
    with StubServer(access=False) as server:
        context, page = open_page(browser, server, VIEWPORTS["desktop"])
        try:
            screen(page, "idle")
            expect(page.locator("[data-test=photo]")).to_be_attached()
            expect(page.locator("[data-test=access]")).to_have_count(0)
        finally:
            context.close()
