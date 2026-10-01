"""Pydantic contracts shared by every pass (LLD section 2)."""

from decimal import Decimal
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
    flags: list[Literal["QUANTITY_ASSUMED", "QUANTITY_INEXACT"]] = []


class CartResult(Contract):
    product_id: str
    status: Literal["added", "failed"]
    quantity_shown: str | None  # as the cart displays it
    message: str | None
