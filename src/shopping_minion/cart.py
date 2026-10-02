"""add_cart pass (LLD sections 3.7 and 7.2 to 7.6).

Works on the product page, one product at a time, never in parallel. The only buttons this
module clicks are the product page's "Adicionar ao carrinho" and the stepper's `+`, plus the
header cart button to open the drawer. Nothing here goes past the cart.
"""

import json
import re
import time
from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Request, Response

from shopping_minion.browser import BASE_URL, CART_BUTTON, dismiss_cookie_banner, settle
from shopping_minion.items import Candidate, CartResult, CartTarget

CLICK_TIMEOUT_MS = 5_000
STEPPER_WAIT_SECONDS = 5.0
PAGE_WAIT_MS = 10_000

# The main buy box of the product page (name, SKU, price, add button or stepper). Seen live
# at 1366x900: `.product-renderer-info-box` is visible and does not contain the "Da mesma
# categoria" cards. `.product-header-summary` also exists but is hidden (a sticky summary).
BUY_BOX = ".product-renderer-info-box"
# The stepper is a div with exactly two direct buttons (trash and `+`) whose text is the
# quantity (site-notes). Classes are utility classes, so it is found by structure.
# The Peso/Unidade switch also has two buttons (role=radio), so it is excluded.
STEPPER = "xpath=.//div[count(./button)=2 and not(./button[@role='radio'])]"
# While the number animates, the stepper briefly holds the old and new values ("1 2"); a
# settled reading is one quantity token, unchanged for STABLE_MS (seen live). Above 1 kg the
# site writes a dot ("1.5kg"; seen 2026-10-02); a comma is kept for other renderings.
QUANTITY_TEXT = re.compile(r"^\d+([.,]\d+)?\s*(g|kg|un)?$", re.IGNORECASE)
STABLE_MS = 300
UNIT_SWITCH_NAME = "Seletor de unidade de venda"

# Logged in, each add or `+` makes the page send its own GraphQL mutation `UpdateCart`, about
# 0.6 s after the click (seen live on 2026-10-01). Leaving the page before its response loses
# the change on the server even though the stepper already shows it: a live run reported
# papel higiênico as added and the real cart didn't have it. So an item only counts as added
# once the page got a 200 for an UpdateCart it sent after the last click. Anonymous carts send
# nothing (they live in the browser), so callers on an anonymous context pass wait_sync=False.
SYNC_OPERATION = "UpdateCart"
SYNC_WAIT_SECONDS = 10.0


def product_url(product_id: str, slug: str) -> str:
    return f"{BASE_URL}/produtos/{product_id}/{slug}"


def _text(locator: Locator) -> str | None:
    """Visible text of the first match with whitespace collapsed, or None if absent."""
    try:
        if not locator.count():
            return None
        return " ".join(locator.first.inner_text(timeout=1_000).split()) or None
    except PlaywrightError:
        return None


def _wait_stepper_change(stepper: Locator, page: Page, before: str | None) -> str | None:
    """Poll until the stepper shows text different from `before`; None on timeout."""
    deadline = time.monotonic() + STEPPER_WAIT_SECONDS
    candidate: str | None = None
    since = 0.0
    while time.monotonic() < deadline:
        now = _text(stepper)
        if now is None or now == before or not QUANTITY_TEXT.match(now):
            candidate = None
        elif now != candidate:
            candidate, since = now, time.monotonic()
        elif (time.monotonic() - since) * 1000 >= STABLE_MS:
            return now
        page.wait_for_timeout(100)
    return None


def _failed(target: CartTarget, message: str, shown: str | None = None) -> CartResult:
    return CartResult(
        product_id=target.product_id, status="failed", quantity_shown=shown, message=message
    )


def _is_sync(request: Request) -> bool:
    """Is this the page's own UpdateCart mutation? Read from what the page sent, never built."""
    if request.method != "POST":
        return False
    try:
        return json.loads(request.post_data or "").get("operationName") == SYNC_OPERATION
    except (ValueError, AttributeError):
        return False


class _SyncWatch:
    """Records when the page sends UpdateCart and when a 200 comes back for it."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.sent: dict[int, float] = {}
        self.confirmed: list[float] = []  # send time of each UpdateCart that got a 200

    def __enter__(self) -> "_SyncWatch":
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc: object) -> None:
        self.page.remove_listener("request", self._on_request)
        self.page.remove_listener("response", self._on_response)

    def _on_request(self, request: Request) -> None:
        if _is_sync(request):
            self.sent[id(request)] = time.monotonic()

    def _on_response(self, response: Response) -> None:
        sent = self.sent.get(id(response.request))
        if sent is not None and response.ok:
            self.confirmed.append(sent)

    def wait_confirmed_after(self, moment: float) -> bool:
        deadline = time.monotonic() + SYNC_WAIT_SECONDS
        while time.monotonic() < deadline:
            if any(sent > moment for sent in self.confirmed):
                return True
            self.page.wait_for_timeout(100)
        return False


def add_to_cart(
    page: Page, candidate: Candidate, target: CartTarget, wait_sync: bool = True
) -> CartResult:
    """Add the product with `target.clicks` clicks and report the quantity the stepper shows.

    With wait_sync (a logged-in cart), the item is `added` only after the site confirmed it.
    """
    if candidate.product_id != target.product_id:
        raise ValueError(f"candidate {candidate.product_id} does not match {target.product_id}")
    try:
        with _SyncWatch(page) as watch:
            return _add(page, candidate, target, watch if wait_sync else None)
    except PlaywrightError as exc:  # includes Playwright timeouts
        return _failed(target, f"erro no navegador: {str(exc).splitlines()[0]}")


def _add(
    page: Page, candidate: Candidate, target: CartTarget, watch: _SyncWatch | None
) -> CartResult:
    page.goto(product_url(candidate.product_id, candidate.slug))
    settle(page)
    box = page.locator(BUY_BOX)
    add_button = box.get_by_role("button", name="Adicionar ao carrinho")
    stepper = box.locator(STEPPER)
    # The page is ready when the buy box shows either the add button or the stepper.
    try:
        add_button.or_(stepper).first.wait_for(state="visible", timeout=PAGE_WAIT_MS)
    except PlaywrightError:
        return _failed(target, "página do produto não carregou")
    dismiss_cookie_banner(page)

    if not add_button.count() or not add_button.first.is_visible():
        return CartResult(
            product_id=target.product_id,
            status="untouched",
            quantity_shown=_text(stepper),
            message="não mexido: já estava no carrinho",
        )

    # Weight products have a Peso/Unidade switch; only Peso is supported (LLD 7.4). The one in
    # the buy box: produce pages also show switches on the suggested products' cards (4 on the
    # page, 1 in the box; seen 2026-10-02, run 11).
    switch = box.get_by_role("radiogroup", name=UNIT_SWITCH_NAME)
    if switch.count() and not switch.get_by_role("radio", name="Peso", exact=True).is_checked():
        return _failed(target, "seletor de unidade não está em Peso")

    shown: str | None = None
    last_click = 0.0
    for click in range(target.clicks):
        last_click = time.monotonic()
        if click == 0:
            add_button.first.click(timeout=CLICK_TIMEOUT_MS)
            settle(page)
        else:
            stepper.first.locator("xpath=./button[last()]").click(timeout=CLICK_TIMEOUT_MS)
            settle(page)
        new = _wait_stepper_change(stepper, page, shown)
        if new is None:
            return _failed(target, f"a quantidade não mudou após o clique {click + 1}", shown)
        shown = new
    if watch is not None and not watch.wait_confirmed_after(last_click):
        return _failed(target, "o site não confirmou a mudança no carrinho", shown)
    return CartResult(
        product_id=target.product_id, status="added", quantity_shown=shown, message=None
    )


def add_all(
    page: Page,
    targets: list[tuple[Candidate, CartTarget]],
    progress: Callable[[int, int, Candidate, CartResult], None] | None = None,
    wait_sync: bool = True,
    should_stop: Callable[[], bool] | None = None,
) -> list[CartResult]:
    """Add the products strictly one after another; a failure does not stop the run.

    `should_stop` is checked before each product (a cancel stops after the current one), so
    the result list can be shorter than `targets`.
    """
    results = []
    for i, (candidate, target) in enumerate(targets, start=1):
        if should_stop is not None and should_stop():
            break
        result = add_to_cart(page, candidate, target, wait_sync=wait_sync)
        results.append(result)
        if progress:
            progress(i, len(targets), candidate, result)
    return results


def read_cart_drawer(page: Page, reload: bool = True) -> list[tuple[str, str]]:
    """Open the header cart drawer and return (name, quantity text) per line. Leaves it open.

    With reload (the default), it first loads the home page again, so the drawer shows the cart
    as the server has it, not what the page we just clicked on believes.

    Each line is a stepper (two direct buttons, quantity text) inside the drawer dialog; its
    name is the first text line of the nearest ancestor that has one besides the quantity and
    prices. Checked live with two products (atum 3, frango 300g).
    """
    if reload:
        page.goto(BASE_URL)
        settle(page)
    page.locator(CART_BUTTON).click(timeout=CLICK_TIMEOUT_MS)
    settle(page)
    drawer = page.get_by_role("dialog")
    drawer.first.wait_for(state="visible", timeout=PAGE_WAIT_MS)
    lines = []
    for stepper in drawer.first.locator(STEPPER).all():
        quantity = _text(stepper)
        # "Instruções | Remover" also has two buttons; only quantity text is a stepper (seen live).
        if quantity is None or not QUANTITY_TEXT.match(quantity):
            continue
        lines.append((_line_name(stepper, quantity), quantity))
    return lines


def _line_name(stepper: Locator, quantity: str) -> str:
    for up in range(1, 6):
        ancestor = stepper.locator(f"xpath=ancestor::*[{up}]")
        for line in ancestor.inner_text().splitlines():
            line = line.strip()
            if line and line != quantity and not line.startswith("R$"):
                return line
    return ""
