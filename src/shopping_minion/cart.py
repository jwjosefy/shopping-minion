"""add_cart pass (LLD sections 3.7 and 7.2 to 7.6).

Works on the product page, one product at a time, never in parallel. The only buttons this
module clicks are the product page's "Adicionar ao carrinho" and the stepper's `+`, plus the
header cart button to open the drawer. Nothing here goes past the cart.
"""

import time
from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from shopping_minion.browser import BASE_URL, CART_BUTTON, dismiss_cookie_banner
from shopping_minion.items import Candidate, CartResult, CartTarget

CLICK_TIMEOUT_MS = 5_000
STEPPER_WAIT_SECONDS = 5.0
PAGE_WAIT_MS = 10_000

# The main buy box of the product page (name, price, add button or stepper). Seen in T4:
# it is a div with this class, and it does not contain the "Da mesma categoria" cards.
BUY_BOX = ".product-header-summary"
# The stepper is a div with exactly two direct buttons (trash and `+`) whose text is the
# quantity (site-notes). Classes are utility classes, so it is found by structure.
STEPPER = "xpath=.//div[count(./button)=2]"
UNIT_SWITCH_NAME = "Seletor de unidade de venda"


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
    while time.monotonic() < deadline:
        now = _text(stepper)
        if now is not None and now != before:
            return now
        page.wait_for_timeout(100)
    return None


def _failed(target: CartTarget, message: str, shown: str | None = None) -> CartResult:
    return CartResult(
        product_id=target.product_id, status="failed", quantity_shown=shown, message=message
    )


def add_to_cart(page: Page, candidate: Candidate, target: CartTarget) -> CartResult:
    """Add the product with `target.clicks` clicks and report the quantity the stepper shows."""
    if candidate.product_id != target.product_id:
        raise ValueError(f"candidate {candidate.product_id} does not match {target.product_id}")
    try:
        return _add(page, candidate, target)
    except PlaywrightError as exc:  # includes Playwright timeouts
        return _failed(target, f"erro no navegador: {str(exc).splitlines()[0]}")


def _add(page: Page, candidate: Candidate, target: CartTarget) -> CartResult:
    page.goto(product_url(candidate.product_id, candidate.slug))
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
        return _failed(target, "já está no carrinho", shown=_text(stepper))

    # Weight products have a Peso/Unidade switch; only Peso is supported (LLD 7.4).
    switch = page.get_by_role("radiogroup", name=UNIT_SWITCH_NAME)
    if switch.count() and not switch.get_by_role("radio", name="Peso", exact=True).is_checked():
        return _failed(target, "seletor de unidade não está em Peso")

    shown: str | None = None
    for click in range(target.clicks):
        if click == 0:
            add_button.first.click(timeout=CLICK_TIMEOUT_MS)
        else:
            stepper.first.locator("xpath=./button[last()]").click(timeout=CLICK_TIMEOUT_MS)
        new = _wait_stepper_change(stepper, page, shown)
        if new is None:
            return _failed(target, f"a quantidade não mudou após o clique {click + 1}", shown)
        shown = new
    return CartResult(
        product_id=target.product_id, status="added", quantity_shown=shown, message=None
    )


def add_all(
    page: Page,
    targets: list[tuple[Candidate, CartTarget]],
    progress: Callable[[int, int, Candidate, CartResult], None] | None = None,
) -> list[CartResult]:
    """Add the products strictly one after another; a failure does not stop the run."""
    results = []
    for i, (candidate, target) in enumerate(targets, start=1):
        result = add_to_cart(page, candidate, target)
        results.append(result)
        if progress:
            progress(i, len(targets), candidate, result)
    return results


def read_cart_drawer(page: Page) -> list[tuple[str, str]]:
    """Open the header cart drawer and return (name, quantity text) per line. Leaves it open.

    Inference, not observed with products in it: each line is a stepper (two direct buttons)
    inside the drawer dialog, and its name is the first text line of the nearest ancestor that
    has one besides the quantity and prices.
    """
    page.locator(CART_BUTTON).click(timeout=CLICK_TIMEOUT_MS)
    drawer = page.get_by_role("dialog")
    drawer.first.wait_for(state="visible", timeout=PAGE_WAIT_MS)
    lines = []
    for stepper in drawer.first.locator(STEPPER).all():
        quantity = _text(stepper)
        if quantity is None:
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
