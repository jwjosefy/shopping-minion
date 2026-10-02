"""Pydantic contracts shared by every pass (LLD section 2)."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quantity(Contract):
    value: float = Field(gt=0)
    unit: Literal["un", "g", "kg", "ml", "l", "pct", "cx", "lata", "dz"]


class Item(Contract):
    """One line item, after OCR and review."""

    source_line: str
    name: str
    search_term: str
    constraints: list[str] = []
    brand: str | None = None
    quantity: Quantity | None = None
    needs_review: bool = False


class Candidate(Contract):
    """One search result."""

    product_id: str
    slug: str  # for /produtos/<id>/<slug> (LLD §7.2)
    name: str
    brand: str | None
    price: Decimal | None
    list_price: Decimal | None  # before discount, if any
    unit_of_sale: Literal["un", "kg"]
    step_kg: float | None  # stepper increment for kg products
    available: bool
    image: str | None = None  # product photo URL; only the web page shows it (LLD-M2 §7.5)


class Decision(Contract):
    item: Item
    candidates: list[Candidate]
    choice: str | None  # product_id, or None for "nenhum"
    confidence: float | None  # Jev's answer; None when the user chose
    probabilities: dict[str, float] = {}
    status: Literal["accepted", "ask", "no_match", "user_chosen", "skipped"]
    model: str | None = None  # Jev version that answered, as the response reports it


class CartTarget(Contract):
    product_id: str
    clicks: int = Field(ge=1)  # adds/+ clicks
    flags: list[Literal["QUANTITY_ASSUMED", "QUANTITY_INEXACT", "QUANTITY_FROM_HISTORY"]] = []


class CartResult(Contract):
    product_id: str
    status: Literal["added", "failed", "untouched"]  # untouched: was already in the cart
    quantity_shown: str | None  # as the cart displays it
    message: str | None


class DraftLine(Contract):
    """One product to add to the cart, with every list item it comes from (LLD-M2 §3, §6)."""

    line_id: str  # the product id
    candidate: Candidate
    items: list[Item]  # several after a merge of duplicates
    quantity: Quantity  # the target (the sum, after a merge)
    target: CartTarget
    flags: list[
        Literal["QUANTITY_ASSUMED", "QUANTITY_INEXACT", "QUANTITY_FROM_HISTORY"]
    ] = []  # same as target.flags
    estimated_price: Decimal | None = None  # price x amount, in code; None without a price


class CartDraft(Contract):
    """What will be added, before the user confirms (LLD-M2 §3)."""

    lines: list[DraftLine]
    skipped: list[Decision]
    estimated_total: Decimal | None  # None when a line has no price, or there are no lines
    warnings: list[str] = []  # e.g. a kg product with no stepper increment, left out


def estimate_price(candidate: Candidate, clicks: int) -> Decimal | None:
    """Price x amount: un -> price x clicks; kg -> price x (clicks x step_kg). In cents."""
    if candidate.price is None:
        return None
    if candidate.unit_of_sale == "kg":
        if candidate.step_kg is None:
            return None
        amount = Decimal(clicks) * Decimal(str(candidate.step_kg))
    else:
        amount = Decimal(clicks)
    return (candidate.price * amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
