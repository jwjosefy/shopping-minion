"""Drive our own web app (static/) with Playwright against the stub backend (LLD-M2 section 8).

Only 127.0.0.1 is touched; Vue itself loads from the CDN, the one external request. The store
is never contacted. Run with: uv run pytest -m live tests/test_ui.py
"""

import re
import time
import urllib.request

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright
from ui_stub import GROUPED_ITEMS, LIST_ITEMS, MANY_PICK, NO_RESULTS_PICK, PICKS, StubServer

pytestmark = pytest.mark.live

VUE_URL = "https://cdn.jsdelivr.net/npm/vue@3.5.43/dist/vue.global.prod.js"
PHOTO = {"name": "lista.png", "mimeType": "image/png", "buffer": b"\x89PNG\r\n\x1a\n"}
PHOTO_2 = {**PHOTO, "name": "lista-2.png"}
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


def set_visibility(page, value):
    page.evaluate(
        "v => {"
        " Object.defineProperty(document, 'visibilityState', {value: v, configurable: true});"
        " document.dispatchEvent(new Event('visibilitychange')); }",
        value,
    )


def send_photos(page, *photos):
    """Pick the photos, one pick each (as a phone's camera does), then press "ler lista"."""
    for photo in photos:
        page.locator("input[data-test=photo]").set_input_files(photo)
    page.locator("[data-test=read-list]").click()


def assert_fits(page, screen):
    width, inner = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert width <= inner, f"{screen}: scrollWidth {width} > innerWidth {inner}"


def is_cart_put(response):
    return response.request.method == "PUT" and response.url.endswith("/api/run/cart-draft")


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
            assert photo.get_attribute("multiple") is not None
            photo.set_input_files(PHOTO)
            expect(page.locator("[data-test=pending-photo]")).to_have_count(1)
            page.locator("[data-test=read-list]").click()
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
            rows = page.locator("[data-test=list-card]")
            expect(rows).to_have_count(3)
            expect(page.locator(".rcard.review")).to_have_count(1)
            assert_fits(page, "reviewing_list")
            photo_box = page.locator("[data-test=list-photo]").bounding_box()
            table_box = page.locator(".rcards").bounding_box()
            if size[0] < 900:  # on a phone the photo sits above the table
                assert photo_box["y"] + photo_box["height"] <= table_box["y"] + 1
            else:  # beside it on a desktop
                assert photo_box["x"] + photo_box["width"] <= table_box["x"] + 1
            # constraints are comma text; edits are saved without pressing anything
            rows.nth(2).get_by_role("button", name="mais").click()
            expect(rows.nth(2).get_by_label("restrições", exact=True)).to_have_value(
                "cremoso, sem lactose"
            )
            rows.nth(0).get_by_role("button", name="mais").click()
            rows.nth(0).get_by_label("nome", exact=True).fill("leite integral UHT")
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
            expect(page.locator("[data-test=pick-term]")).to_have_text("busca: “tomate italiano”")
            expect(page.locator("[data-test=jev-note]")).to_contain_text("0,56")
            cards = page.locator(".cand")
            expect(cards).to_have_count(3)
            expect(cards.nth(0)).to_have_attribute("data-test", "cand-t-jev")
            expect(cards.nth(0)).to_have_class(re.compile(r"\bselected\b"))
            expect(cards.nth(0)).to_contain_text("R$")
            expect(cards.nth(0).locator("s")).to_contain_text("9,00")
            expect(cards.nth(0).locator("[data-test=badge-offer]")).to_have_text("oferta")
            expect(page.locator("[data-test=badge-offer]")).to_have_count(1)
            # the quantity sits under the selected card, from the list (1 kg), no badge
            expect(page.locator("[data-test=pick-qty]")).to_have_count(1)
            expect(cards.nth(0).locator("[data-test=pick-qty-value]")).to_have_value("1")
            expect(cards.nth(0).locator("[data-test=pick-qty-unit]")).to_have_value("kg")
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

            # 6. Resumo: one collapsed line per item, then "Revisar tudo" for the full editor
            cart = screen(page, "reviewing_cart")
            expect(cart.get_by_role("heading", name="Resumo")).to_be_visible()
            summary = page.locator("[data-test=summary-line]")
            expect(summary).to_have_count(5)  # leite, tomate, requeijão x2, and the skipped one
            expect(summary.nth(0)).to_contain_text("leite integral")
            expect(summary.nth(0)).to_contain_text("Leite integral 1L")
            expect(summary.nth(0).locator("[data-test=summary-qty]")).to_have_text("1 un")
            expect(summary.nth(0).locator("[data-test=badge-history]")).to_have_text(
                "mesma quantidade da última vez que comprou este produto"
            )
            expect(summary.nth(0).locator("[data-test=badge-offer]")).to_have_text("oferta")
            expect(summary.nth(1).locator("[data-test=badge-inexact]")).to_have_text("aproximada")
            expect(summary.nth(2).locator("[data-test=badge-assumed]")).to_have_text(
                "quantidade assumida"
            )
            expect(summary.nth(4)).to_contain_text("pulado")
            expect(page.locator("[data-test=total]")).to_contain_text("R$")
            expect(cart).to_contain_text("estimado")
            expect(page.locator("[data-test=trocar]")).to_have_count(5)
            expect(page.locator("[data-test=cart-line]")).to_have_count(0)  # collapsed: no editor
            assert_fits(page, "reviewing_cart (summary)")
            page.locator("[data-test=review-all]").click()
            expect(cart.get_by_role("heading", name="Revisar tudo")).to_be_visible()
            lines = page.locator("[data-test=cart-line]")
            expect(lines).to_have_count(3)
            expect(cart).to_contain_text("quantidade assumida")
            expect(cart).to_contain_text("aproximada")
            expect(cart).to_contain_text("mesma quantidade da última vez que comprou este produto")
            expect(cart).to_contain_text("2 linhas da lista")
            expect(page.locator("[data-test=skipped]")).to_contain_text("requeijão")
            expect(page.locator("[data-test=total]")).to_contain_text("R$")
            expect(cart).to_contain_text("estimado")
            assert_fits(page, "reviewing_cart (revisar tudo)")
            clicks = page.locator("[data-test=clicks-p-tomate]")
            expect(clicks).to_have_text("2")  # 1 kg in steps of 0.5 kg
            total_before = page.locator("[data-test=total]").inner_text()
            qty = page.locator("[data-test=qty-p-tomate]")
            with page.expect_response(is_cart_put):  # the PUT's answer, then the assertions
                qty.fill("2")
                qty.blur()
            expect(clicks).to_have_text("4")
            assert stub.cart_puts[-1] == {
                "lines": [
                    {"line_id": "p-tomate", "quantity": {"value": 2, "unit": "kg"}, "remove": False}
                ]
            }
            expect(page.locator("[data-test=total]")).not_to_have_text(total_before)
            with page.expect_response(is_cart_put):
                page.locator("[data-test=remove-p-leite]").click()
            expect(lines).to_have_count(2)
            assert stub.cart_puts[-1]["lines"] == [
                {"line_id": "p-leite", "quantity": None, "remove": True}
            ]
            # back to the summary: it follows what the editor changed
            page.locator("[data-test=back-to-summary]").click()
            summary = page.locator("[data-test=summary-line]")
            expect(summary.nth(0)).to_contain_text("removido do carrinho")
            expect(summary.nth(1).locator("[data-test=summary-qty]")).to_have_text("2 kg")
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
            # the report: time per step, total and corrections, under the failures
            report = page.locator("[data-test=report]")
            expect(report.locator("[data-test=report-steps] li")).to_have_count(3)
            expect(report.locator("[data-test=report-steps]")).to_contain_text("6 min 05 s")
            expect(report.locator("[data-test=report-total]")).to_contain_text("total 10 min 59 s")
            expect(report.locator("[data-test=report-corrections]")).to_contain_text(
                "lista: 1 linha editada, 0 apagadas, 2 adicionadas (de 3 lidas pelo OCR)"
            )
            expect(report.locator("[data-test=report-corrections]")).to_contain_text(
                "1 aceito pelo Jev sozinho"
            )
            expect(report.locator("[data-test=report-corrections]")).to_contain_text(
                "1 quantidade mudada, 1 removido"
            )
            problems_box = page.locator("[data-test=problems]").bounding_box()
            assert report.bounding_box()["y"] > problems_box["y"] + problems_box["height"]
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
            send_photos(page, PHOTO)
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


def test_an_item_with_no_results_can_be_skipped(browser):
    with StubServer(picks=[NO_RESULTS_PICK]) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            send_photos(page, PHOTO)
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
            send_photos(page, PHOTO)
            screen(page, "reading_list")
            server.stub.release("reading_list")
            screen(page, "reviewing_list")
            # the run moves on behind the page's back; the next call answers 409 and resyncs
            server.stub.go("reviewing_cart")
            # The event stream may redraw the page before the click lands; either path must
            # end on the run's real state (a 409 resync, or the stream's own state event).
            try:
                page.locator("[data-test=confirm-list]").click(timeout=2_000)
            except PlaywrightError:
                pass
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


def test_reconnects_when_the_page_comes_back_and_shows_a_banner_only_while_down(browser):
    with StubServer() as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            banner = page.locator("[data-test=reconnecting]")
            expect(banner).to_have_count(0)
            wait_for(lambda: len(stub.stream_opens) >= 1, "the first stream")

            # back to the foreground: the stream is reopened and the snapshot reloaded
            opens, snapshots = len(stub.stream_opens), stub.snapshots
            set_visibility(page, "visible")
            wait_for(lambda: len(stub.stream_opens) > opens, "the stream reopened")
            wait_for(lambda: stub.snapshots > snapshots, "the snapshot reloaded")
            expect(banner).to_have_count(0)

            # hidden does nothing
            opens = len(stub.stream_opens)
            set_visibility(page, "hidden")
            time.sleep(0.3)
            assert len(stub.stream_opens) == opens

            # the stream drops: banner while down, gone when it is back
            stub.events_down = True
            set_visibility(page, "visible")
            expect(banner).to_be_visible()
            expect(banner).to_have_text("reconectando…")
            assert_fits(page, "reconnecting banner")
            stub.events_down = False
            expect(banner).to_have_count(0, timeout=6000)
        finally:
            context.close()


def test_several_photos_are_sent_together_and_shown_as_tabs(browser):
    with StubServer(hold={"reading_list"}) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            expect(page.locator("[data-test=read-list]")).to_have_count(0)
            page.locator("input[data-test=photo]").set_input_files([PHOTO, PHOTO_2])
            pending = page.locator("[data-test=pending-photo]")
            expect(pending).to_have_count(2)
            assert_fits(page, "idle with two photos")
            pending.nth(0).get_by_role("button", name="remover página 1").click()
            expect(pending).to_have_count(1)
            page.locator("input[data-test=photo]").set_input_files(
                PHOTO
            )  # one more, as a camera does
            expect(pending).to_have_count(2)
            page.locator("[data-test=read-list]").click()
            wait_for(lambda: stub.uploads == ["lista-2.png", "lista.png"], "both photos, in order")
            screen(page, "reading_list")
            stub.release("reading_list")
            screen(page, "reviewing_list")
            tabs = page.locator("[data-test=photo-tab]")
            expect(tabs).to_have_count(2)
            image = page.locator("[data-test=list-photo]")
            first = image.get_attribute("src")
            tabs.nth(1).click()
            wait_for(lambda: image.get_attribute("src") != first, "the second page")
            assert_fits(page, "reviewing_list with tabs")
        finally:
            context.close()


def test_the_sixth_photo_is_refused(browser):
    with StubServer() as server:
        context, page = open_page(browser, server, VIEWPORTS["desktop"])
        try:
            screen(page, "idle")
            page.locator("input[data-test=photo]").set_input_files([PHOTO] * 5)
            expect(page.locator("[data-test=pending-photo]")).to_have_count(5)
            expect(page.locator("input[data-test=photo]")).to_be_disabled()
        finally:
            context.close()


def test_the_review_card_groups_a_line_read_as_several_items(browser):
    with StubServer(items=LIST_ITEMS + GROUPED_ITEMS) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            send_photos(page, PHOTO)
            screen(page, "reviewing_list")
            cards = page.locator("[data-test=list-card]")
            expect(cards).to_have_count(4)  # three lines, the last one holding two items
            # by default: the line (read-only) and the search; the rest is behind "mais"
            leite = cards.nth(0)
            expect(leite.locator("[data-test=line]")).to_have_text("leite int. 1l")
            expect(leite.locator("input[aria-label=linha]")).to_have_count(0)  # read-only text
            expect(leite.get_by_label("busca", exact=True)).to_have_value("leite integral 1l")
            expect(leite.get_by_label("quantidade", exact=True)).to_have_value(
                "1"
            )  # the list has one
            expect(leite.get_by_label("nome", exact=True)).to_have_count(0)
            expect(leite.get_by_label("marca", exact=True)).to_have_count(0)
            # no quantity on the list: a link instead of a field
            req = cards.nth(2)
            expect(req.get_by_label("quantidade", exact=True)).to_have_count(0)
            req.locator("[data-test=add-qty]").click()
            expect(req.get_by_label("quantidade", exact=True)).to_be_visible()
            # one card, one line, two "busca" fields
            group = cards.nth(3)
            expect(group.locator("[data-test=line]")).to_have_count(1)
            expect(group.get_by_label("busca", exact=True)).to_have_count(2)
            expect(group.get_by_label("busca", exact=True).nth(1)).to_have_value(
                "saco de lixo banheiro"
            )
            group.get_by_role("button", name="mais").click()
            expect(group.get_by_label("nome", exact=True)).to_have_count(2)
            expect(group.get_by_label("restrições", exact=True).nth(1)).to_have_value("banheiro")
            group.get_by_role("button", name="menos").click()
            expect(group.get_by_label("nome", exact=True)).to_have_count(0)
            assert_fits(page, "reviewing_list grouped")
            # deleting one field deletes that item; the other stays
            group.get_by_role("button", name="remover busca").first.click()
            expect(group.get_by_label("busca", exact=True)).to_have_count(1)
            expect(group.get_by_label("busca", exact=True)).to_have_value("saco de lixo banheiro")
            wait_for(lambda: stub.list_puts and len(stub.list_puts[-1]["items"]) == 4, "the save")
            saved = stub.list_puts[-1]["items"]
            assert [i["name"] for i in saved][-1] == "saco de lixo (banheiro)"
            assert saved[0] == {
                **LIST_ITEMS[0],
                "alternatives": [],
            }  # an untouched item goes back as it came
        finally:
            context.close()


def test_the_review_card_edits_the_alternatives_under_busca(browser):
    meat = {
        **LIST_ITEMS[0],
        "source_line": "carne de panela (acém ou paleta)",
        "name": "carne de panela",
        "search_term": "acém",
        "alternatives": ["paleta"],
        "constraints": [],
        "quantity": None,
    }
    with StubServer(items=[meat]) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "idle")
            send_photos(page, PHOTO)
            screen(page, "reviewing_list")
            card = page.locator("[data-test=list-card]")
            expect(card.get_by_label("busca", exact=True)).to_have_value("acém")
            expect(card.get_by_label("ou", exact=True)).to_have_value("paleta")
            # add one, fill it, and remove the first
            card.locator("[data-test=add-alt]").click()
            card.get_by_label("ou", exact=True).nth(1).fill("peito")
            card.get_by_role("button", name="remover ou").first.click()
            expect(card.get_by_label("ou", exact=True)).to_have_count(1)
            expect(card.get_by_label("ou", exact=True)).to_have_value("peito")
            assert_fits(page, "reviewing_list alternatives")
            wait_for(
                lambda: (
                    stub.list_puts and stub.list_puts[-1]["items"][0]["alternatives"] == ["peito"]
                ),
                "the save",
            )
        finally:
            context.close()


def to_picking(page):
    screen(page, "idle")
    send_photos(page, PHOTO)
    screen(page, "reviewing_list")
    page.locator("[data-test=confirm-list]").click()
    screen(page, "picking")


def test_the_no_results_card_searches_a_new_term(browser):
    with StubServer(picks=[NO_RESULTS_PICK]) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            to_picking(page)
            expect(page.locator("[data-test=no-results]")).to_be_visible()
            search = page.locator("[data-test=search-again]")
            expect(search).to_be_disabled()  # nothing typed yet
            assert_fits(page, "picking (no results, search field)")
            page.locator("[data-test=new-term]").fill("sal moído")
            search.click()
            wait_for(lambda: stub.searches == [{"index": 0, "term": "sal moído"}], "the search")

            # the picker reloads the item with the new cards; the user picks
            expect(page.locator(".cand")).to_have_count(2)
            expect(page.locator("[data-test=pick-term]")).to_have_text("busca: “sal moído”")
            expect(page.locator("[data-test=no-results]")).to_have_count(0)
            expect(page.locator("[data-test=new-search]")).to_have_count(0)
            expect(page.locator("[data-test=badge-offer]")).to_have_count(1)  # sg-2 is on offer
            assert page.locator(".cand.selected").count() == 0  # no Jev pick: nothing preselected
            assert stub.picks_posted == []
            page.locator("[data-test=cand-sg-1]").click()
            expect(page.locator("[data-test=pick-qty-value]")).to_have_value("1")
            expect(page.locator("[data-test=badge-assumed]")).to_have_text("quantidade assumida")
            assert_fits(page, "picking (after the new search)")
            page.locator("[data-test=take]").click()
            wait_for(lambda: stub.picks_posted == [{"index": 0, "product_id": "sg-1"}], "the pick")
            screen(page, "reviewing_cart")
        finally:
            context.close()


def test_the_picker_header_stays_on_top_and_the_quantity_follows_the_selection(browser):
    with StubServer(picks=[MANY_PICK, PICKS[1]]) as server:
        stub = server.stub
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            to_picking(page)
            head = page.locator("[data-test=pick-head]")
            expect(head).to_contain_text("arroz")
            expect(head).to_contain_text("busca: “arroz agulhinha 5kg”")
            assert_fits(page, "picking (many cards)")
            page.locator("[data-test=cand-a-11]").scroll_into_view_if_needed()
            assert page.evaluate("window.scrollY") > 400  # the cards scrolled
            top = head.bounding_box()["y"]
            assert 60 <= top <= 80, top  # the header stayed, right under the top bar
            expect(head).to_be_in_viewport()

            # the quantity is under the selected card only, and follows the selection
            expect(page.locator("[data-test=pick-qty]")).to_have_count(1)
            page.locator("[data-test=cand-a-11]").click()
            expect(page.locator("[data-test=cand-a-11] [data-test=pick-qty]")).to_have_count(1)
            expect(page.locator("[data-test=pick-qty]")).to_have_count(1)
            assert stub.picks_posted == []  # selecting is not confirming
            expect(page.locator("[data-test=badge-assumed]")).to_have_count(1)
            # editing it drops the badge: the number is now the user's
            value = page.locator("[data-test=pick-qty-value]")
            value.fill("3")
            expect(page.locator("[data-test=badge-assumed]")).to_have_count(0)
            page.locator("[data-test=cand-a-10]").click()  # another card: the edit stays
            expect(value).to_have_value("3")
            page.locator("[data-test=take]").click()
            wait_for(
                lambda: (
                    stub.picks_posted
                    == [{"index": 0, "product_id": "a-10", "quantity": {"value": 3, "unit": "un"}}]
                ),
                "the pick with the quantity",
            )

            # the next item: each card has its own prefill; untouched, none is sent
            expect(page.locator("[data-test=pick-item]")).to_have_text("requeijão")
            page.locator("[data-test=cand-r-1]").click()
            expect(page.locator("[data-test=pick-qty-value]")).to_have_value("2")
            expect(page.locator("[data-test=badge-history]")).to_have_text(
                "mesma quantidade da última vez que comprou este produto"
            )
            assert_fits(page, "picking (history badge)")
            page.locator("[data-test=cand-r-2]").click()
            expect(page.locator("[data-test=pick-qty-value]")).to_have_value("1")
            expect(page.locator("[data-test=badge-assumed]")).to_have_count(1)
            expect(page.locator("[data-test=badge-history]")).to_have_count(0)
            page.locator("[data-test=cand-r-1]").click()
            page.locator("[data-test=pick-qty-value]").press("Enter")  # Enter in the field confirms
            wait_for(lambda: len(stub.picks_posted) == 2, "the second pick")
            assert stub.picks_posted[1] == {"index": 1, "product_id": "r-1"}
            screen(page, "reviewing_cart")
        finally:
            context.close()


def test_trocar_goes_back_to_one_item_and_returns_to_the_summary(browser):
    with StubServer() as server:
        stub = server.stub
        stub.go("reviewing_cart")
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "reviewing_cart")
            summary = page.locator("[data-test=summary-line]")
            expect(summary).to_have_count(4)
            assert_fits(page, "summary")
            expect(summary.nth(3)).to_contain_text("requeijão")
            expect(summary.nth(2).locator("[data-test=badge-assumed]")).to_be_visible()
            page.locator("[data-test=trocar]").nth(0).click()
            wait_for(lambda: stub.reopens == [{"index": 0}], "the reopen")

            # that one item, back on its cards, with the amount it had
            screen(page, "picking")
            expect(page.locator("[data-test=pick-item]")).to_have_text("leite integral")
            expect(page.locator("[data-test=pick-count]")).to_have_text(
                "trocando a escolha deste item"
            )
            expect(page.locator(".cand")).to_have_count(2)
            expect(page.locator(".cand.selected")).to_have_attribute("data-test", "cand-p-leite")
            expect(page.locator("[data-test=pick-qty-value]")).to_have_value("1")
            expect(page.locator("[data-test=jev-note]")).to_have_count(0)
            assert_fits(page, "picking (trocar)")
            page.locator("[data-test=pick-qty-value]").fill("3")
            page.locator("[data-test=take]").click()
            wait_for(
                lambda: (
                    stub.picks_posted
                    == [
                        {
                            "index": 0,
                            "product_id": "p-leite",
                            "quantity": {"value": 3, "unit": "un"},
                        }
                    ]
                ),
                "the new pick",
            )

            # after it is picked, the summary again, with the new amount (and no flag)
            screen(page, "reviewing_cart")
            expect(summary).to_have_count(4)
            expect(summary.nth(0).locator("[data-test=summary-qty]")).to_have_text("3 un")
            expect(summary.nth(0).locator("[data-test=badge-history]")).to_have_count(0)
        finally:
            context.close()


def test_badges_use_one_color_per_meaning(browser):
    with StubServer() as server:
        stub = server.stub
        stub.go("reviewing_cart")
        context, page = open_page(browser, server, VIEWPORTS["desktop"])
        try:
            screen(page, "reviewing_cart")
            colors = {}
            for name, text in {
                "assumed": "quantidade assumida",
                "history": "mesma quantidade da última vez que comprou este produto",
                "inexact": "aproximada",
                "offer": "oferta",
            }.items():
                badge = page.locator(f"[data-test=badge-{name}]").first
                expect(badge).to_have_text(text)
                colors[name] = badge.evaluate(
                    "(el, token) => [getComputedStyle(el).color,"
                    " getComputedStyle(document.documentElement).getPropertyValue(token).trim()]",
                    f"--badge-{name}",
                )
                rgb, token = colors[name]
                assert token.startswith("#"), (name, token)  # defined once, as a token
            assert len({rgb for rgb, _ in colors.values()}) == 4  # four meanings, four colors
        finally:
            context.close()


def test_the_done_screen_shows_the_report_under_the_outcome(browser):
    with StubServer() as server:
        server.stub.go("done")
        context, page = open_page(browser, server, VIEWPORTS["phone"])
        try:
            screen(page, "done")
            report = page.locator("[data-test=report]")
            expect(report).to_be_visible()
            expect(report.locator("[data-test=report-steps] li")).to_have_count(3)
            expect(report.locator("[data-test=report-steps] li").nth(1)).to_contain_text(
                "escolhendo produtos"
            )
            expect(report.locator("[data-test=report-steps] li").nth(1)).to_contain_text("você")
            expect(report.locator("[data-test=report-total]")).to_contain_text(
                "máquina 4 min 54 s, você 6 min 05 s"
            )
            assert_fits(page, "done with the report")
            page.locator("[data-test=new-list]").click()
            screen(page, "idle")
            expect(page.locator("[data-test=report]")).to_have_count(0)
        finally:
            context.close()
