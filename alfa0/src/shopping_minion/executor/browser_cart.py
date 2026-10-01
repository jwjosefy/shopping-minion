"""Cart executor that uses the store's site in a browser, the way a user does (ADR-0012, LLD 2.5).

`BrowserCartExecutor.add_to_cart` reads the cart, finds the product on the search results page,
clicks add and sets the quantity inside its card, then reads the cart back and checks the line.
It never sends a request of its own, never logs in, and never touches checkout or payment: every
step list runs with the profile's `checkout_markers` as forbidden.

The decisions are plain functions (`normalize_name`, `line_matches`, `find_line`,
`quantity_matches`, `stepper_clicks`) and are unit-tested. The page-driving methods are thin.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from typing import Literal

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

from shopping_minion.catalog.page import ResponseLog, read_cart_lines, run_steps
from shopping_minion.catalog.profile import (
    Cart,
    ClickStep,
    FieldQuantity,
    FillStep,
    SiteProfile,
    StepperQuantity,
)
from shopping_minion.catalog.reading import ReadLine
from shopping_minion.contracts import Candidate, CartLine, SaleQuantity
from shopping_minion.workflow import SiteChangedError

QuantityUnit = Literal["count", "g", "kg"]

SETTLE_TIMEOUT_MS = 5000  # how long to wait for the page's own requests to go quiet after an action
FIND_TIMEOUT_MS = 15000


class CartConflictError(RuntimeError):
    """The product is already in the cart with a quantity other than the one asked for.

    Changing an existing line is out of scope for now: the run stops instead of guessing.
    """


# --- pure decisions ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Lower case, no accents, single spaces: what "the same name" means for a cart line."""
    decomposed = unicodedata.normalize("NFKD", name)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(bare.casefold().split())


def line_matches(line: Candidate, candidate: Candidate) -> bool:
    """A cart line is the candidate when both have an id and it is equal, else when the names
    are equal ignoring case and accents."""
    if line.id and candidate.id and line.id == candidate.id:
        return True
    return normalize_name(line.name) == normalize_name(candidate.name)


def find_line(lines: Sequence[ReadLine], candidate: Candidate) -> ReadLine | None:
    """The first cart line that is the candidate, or None."""
    return next((line for line in lines if line_matches(line.candidate, candidate)), None)


def quantity_matches(cart_quantity: float, sale: SaleQuantity, unit: QuantityUnit) -> bool:
    """Does the number the cart shows mean the quantity in `sale`?

    `count`: it equals `sale.steps_or_units`. `g` / `kg`: it (times 1000 for kg) equals
    `sale.effective_amount`, when that amount is in grams. When the units can't be compared
    (a product sold by unit, with a cart in grams), it falls back to `count`.
    """
    if unit in ("g", "kg") and sale.effective_unit == "g":
        grams = cart_quantity * 1000 if unit == "kg" else cart_quantity
        return math.isclose(grams, sale.effective_amount, rel_tol=1e-6, abs_tol=1e-6)
    return math.isclose(cart_quantity, sale.steps_or_units, rel_tol=1e-6, abs_tol=1e-6)


def stepper_clicks(target: int) -> int:
    """Clicks of a stepper after adding one unit: `target - 1`, never negative."""
    return max(target - 1, 0)


def _describe(line: ReadLine, unit: QuantityUnit) -> str:
    if line.quantity is None:
        return "an unknown quantity"
    return f"{line.quantity:g}" + ("" if unit == "count" else f" {unit}")


# --- the executor -----------------------------------------------------------------------------


class BrowserCartExecutor:
    def __init__(self, profile: SiteProfile, context: BrowserContext) -> None:
        if profile.cart is None:
            raise ValueError(f"site profile {profile.store!r} has no 'cart' section")
        if profile.search is None:
            raise ValueError(f"site profile {profile.store!r} has no 'search' section")
        self._profile = profile
        self._cart: Cart = profile.cart
        self._context = context
        self._page: Page | None = None
        self._log = ResponseLog()

    async def _get_page(self) -> Page:
        if self._page is None:
            self._page = await self._context.new_page()
            self._log.attach(self._page)
        return self._page

    async def close(self) -> None:
        if self._page is not None:
            page, self._page = self._page, None
            await page.close()

    async def add_to_cart(self, candidate: Candidate, sale: SaleQuantity) -> CartLine:
        page = await self._get_page()
        unit = self._cart.read.quantity_unit
        values = {
            "base_url": self._profile.base_url,
            "query": candidate.name,
            "name": candidate.name,
            "quantity": str(sale.steps_or_units),
        }

        # 1. What the cart holds now. An empty cart may show nothing to read: that is fine here.
        existing = find_line(await self._read_cart(page, values, empty_is_ok=True), candidate)
        if existing is not None:
            if existing.quantity is not None and quantity_matches(existing.quantity, sale, unit):
                return CartLine(
                    product_id=candidate.id, quantity=sale.steps_or_units, verified=True
                )
            raise CartConflictError(
                f"{candidate.name!r} is already in the cart with {_describe(existing, unit)}; "
                f"asked for {sale.steps_or_units} ({sale.effective_amount:g} "
                f"{sale.effective_unit}). Changing an existing line is not supported."
            )

        # 2-3. Find the product like a user, add it, set the quantity, all inside its card.
        await self._add(page, candidate, sale, values)

        # 4. Read the cart back and check the line.
        line = find_line(await self._read_cart(page, values), candidate)
        if line is None:
            raise SiteChangedError(
                f"{candidate.name!r} is not in the cart after adding it: the site may have changed"
            )
        if self._cart.read.quantity is None or line.quantity is None:
            return CartLine(product_id=candidate.id, quantity=sale.steps_or_units, verified=False)
        if not quantity_matches(line.quantity, sale, unit):
            raise SiteChangedError(
                f"{candidate.name!r} is in the cart with {_describe(line, unit)}, expected "
                f"{sale.steps_or_units} ({sale.effective_amount:g} {sale.effective_unit}): "
                "the site may have changed"
            )
        return CartLine(product_id=candidate.id, quantity=sale.steps_or_units, verified=True)

    async def _read_cart(
        self, page: Page, values: dict[str, str], *, empty_is_ok: bool = False
    ) -> list[ReadLine]:
        self._log.clear()  # only responses that arrive from now on describe the cart
        await run_steps(page, self._cart.read.steps, values, never=self._cart.checkout_markers)
        return await read_cart_lines(
            page,
            self._cart.read,
            base_url=self._profile.base_url,
            log=self._log,
            empty_is_ok=empty_is_ok,
        )

    async def _add(
        self, page: Page, candidate: Candidate, sale: SaleQuantity, values: dict[str, str]
    ) -> None:
        search = self._profile.search
        assert search is not None  # checked in __init__
        never = self._cart.checkout_markers

        self._log.clear()
        await run_steps(page, search.steps, values, never=never)
        try:
            await page.wait_for_selector(search.results.wait_for, timeout=FIND_TIMEOUT_MS)
            card = page.locator(self._cart.product_card).filter(has_text=candidate.name).first
            await card.wait_for(timeout=FIND_TIMEOUT_MS)
        except PlaywrightError as error:
            raise SiteChangedError(
                f"no product card containing {candidate.name!r} on the search results "
                f"({type(error).__name__}): the site may have changed"
            ) from error

        await run_steps(page, self._cart.add, values, never=never, root=card)
        await self._settle(page)
        quantity = self._cart.quantity
        if isinstance(quantity, StepperQuantity):
            for _ in range(stepper_clicks(sale.steps_or_units)):
                await run_steps(
                    page, [ClickStep(click=quantity.click)], values, never=never, root=card
                )
                await self._settle(page)
        elif isinstance(quantity, FieldQuantity):
            fill = FillStep(fill=quantity.fill, value="{quantity}")
            await run_steps(page, [fill, *quantity.then], values, never=never, root=card)
            await self._settle(page)

    @staticmethod
    async def _settle(page: Page) -> None:
        """Let the page finish the requests it made itself; carry on if it never goes quiet."""
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        except PlaywrightError:
            pass  # a page that keeps polling never goes idle; the cart read still checks the result
