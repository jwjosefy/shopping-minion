"""The steps of shopping a list, with no terminal I/O (LLD-M2 section 3).

The CLI (`run.py`) and the web app both call these. Progress goes out through callbacks;
nothing here reads input or prints. The browser work stays in search.py and cart.py, the
model call in decide.py, the check in reconcile.py.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from playwright.sync_api import Page

from shopping_minion.cart import add_all, read_cart_drawer
from shopping_minion.config import DecideConfig
from shopping_minion.decide import decide
from shopping_minion.history import ItemHistory
from shopping_minion.items import (
    Candidate,
    CartDraft,
    CartResult,
    Contract,
    Decision,
    DraftLine,
    Item,
    Quantity,
)
from shopping_minion.merge import build_line, line_label, merge_lines
from shopping_minion.preferences import find_preference
from shopping_minion.quantity import target_quantity
from shopping_minion.reconcile import Check, reconcile
from shopping_minion.search import search_all

CartLines = list[tuple[str, str]]  # (product name, quantity text), as the drawer shows them

SearchProgress = Callable[[int, int, Item, list[Candidate]], None]
FillProgress = Callable[[int, int, Candidate, CartResult], None]


class DraftEdit(Contract):
    """A user edit of one draft line. `quantity` None with `remove` False leaves it alone."""

    line_id: str
    quantity: Quantity | None = None
    remove: bool = False


class CartOutcome(Contract):
    before: CartLines | None  # None: the cart couldn't be read (see before_error)
    results: list[CartResult]  # one per attempted draft line, in order (fewer if stopped)
    after: CartLines | None
    checks: list[Check]  # one per draft line, in order; empty if `after` is None
    extras: CartLines  # in the final cart, not in the draft
    before_error: str | None = None
    after_error: str | None = None
    stopped: bool = False  # should_stop ended the cart pass early


# --- search and decide ----------------------------------------------------------------------


def search_list(
    page: Page, items: list[Item], progress: SearchProgress | None = None
) -> list[list[Candidate]]:
    """Up to 15 candidates per item; `progress(i, total, item, candidates)` after each."""
    return search_all(page, items, progress)


def decide_list(
    items: list[Item],
    candidates: list[list[Candidate]],
    prefs: dict[str, dict],
    config: DecideConfig,
    client: Any,
    histories: list[ItemHistory] | None = None,
) -> list[Decision]:
    """Jev's decision per item, in the order of `items`. `histories`, if given, has one entry
    per item, in the same order."""
    return decide(list(zip(items, candidates, strict=True)), prefs, config, client, histories)


# --- the cart draft -------------------------------------------------------------------------


def _total(lines: list[DraftLine]) -> Decimal | None:
    prices = [line.estimated_price for line in lines]
    if not prices or any(price is None for price in prices):
        return None  # a partial sum would read as a real total
    return sum(prices, Decimal(0))


def draft_cart(
    decisions: list[Decision],
    prefs: dict[str, dict],
    histories: list[ItemHistory] | None = None,
) -> CartDraft:
    """Targets and clicks for each decided item, duplicates merged (LLD-M2 section 6).
    `histories`, if given, has one entry per decision, in the same order; the chosen product's
    last quantity is the fallback before the 1 un default."""
    singles: list[DraftLine] = []
    skipped: list[Decision] = []
    warnings: list[str] = []
    for index, decision in enumerate(decisions):
        if decision.status not in ("accepted", "user_chosen") or decision.choice is None:
            skipped.append(decision)
            continue
        candidate = next(c for c in decision.candidates if c.product_id == decision.choice)
        history = histories[index].products.get(decision.choice) if histories else None
        quantity, flags = target_quantity(
            decision.item, find_preference(prefs, decision.item), history, candidate.unit_of_sale
        )
        try:
            line = build_line(
                candidate,
                [decision.item],
                quantity,
                assumed="QUANTITY_ASSUMED" in flags,
                from_history="QUANTITY_FROM_HISTORY" in flags,
            )
        except ValueError as exc:  # a kg product with no stepper increment
            warnings.append(f"{decision.item.name}: {exc}; item pulado.")
            skipped.append(decision)
            continue
        singles.append(line)
    lines = merge_lines(singles)
    return CartDraft(lines=lines, skipped=skipped, estimated_total=_total(lines), warnings=warnings)


def edit_draft(draft: CartDraft, edits: list[DraftEdit]) -> CartDraft:
    """Apply the user's edits: a new quantity recomputes clicks and flags (it is no longer
    assumed); `remove` drops the line, and its items go to `skipped`. A line id the draft
    doesn't have raises ValueError."""
    by_id = {edit.line_id: edit for edit in edits}
    unknown = by_id.keys() - {line.line_id for line in draft.lines}
    if unknown:
        raise ValueError(f"unknown draft line(s): {', '.join(sorted(unknown))}")
    lines: list[DraftLine] = []
    skipped = list(draft.skipped)
    for line in draft.lines:
        edit = by_id.get(line.line_id)
        if edit is None or (edit.quantity is None and not edit.remove):
            lines.append(line)
        elif edit.remove:
            skipped.extend(
                Decision(
                    item=item,
                    candidates=[line.candidate],
                    choice=None,
                    confidence=None,
                    status="skipped",
                )
                for item in line.items
            )
        else:
            lines.append(build_line(line.candidate, line.items, edit.quantity))
    return CartDraft(
        lines=lines,
        skipped=skipped,
        estimated_total=_total(lines),
        warnings=draft.warnings,
    )


# --- filling the cart -----------------------------------------------------------------------


def _read(page: Page) -> tuple[CartLines | None, str | None]:
    try:
        return read_cart_drawer(page), None
    except Exception as exc:  # the run goes on; the outcome says what couldn't be checked
        return None, str(exc)


def fill_cart(
    page: Page,
    draft: CartDraft,
    progress: FillProgress | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> CartOutcome:
    """Read the cart, add every line one after another, read it again, and reconcile.

    The expectation for a line is its summed target, so a merged duplicate is checked for
    the total. `progress(i, total, candidate, result)` after each product. If `should_stop`
    returns True before a product, nothing more is added (`stopped`), the cart is still read
    and checked, and the lines not attempted show as missing unless they were already there.
    """
    before, before_error = _read(page)
    targets = [(line.candidate, line.target) for line in draft.lines]
    results = add_all(page, targets, progress, should_stop=should_stop)
    after, after_error = _read(page)
    checks: list[Check] = []
    extras: CartLines = []
    if after is not None:
        planned = [(line_label(line), line.candidate, line.target) for line in draft.lines]
        checks, extras = reconcile(planned, before, after)
    return CartOutcome(
        before=before,
        results=results,
        after=after,
        checks=checks,
        extras=extras,
        before_error=before_error,
        after_error=after_error,
        stopped=len(results) < len(draft.lines),
    )
