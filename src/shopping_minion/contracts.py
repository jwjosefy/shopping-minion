"""Contracts between components (HLD §9).

Every component boundary passes one of these models. They are deliberately small; fields are
added when a component needs them, not in anticipation.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Probability = float  # always validated to [0, 1] where used


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _ignore_computed_fields(cls, data: Any) -> Any:
        """Saved JSON includes computed fields (confidence, counts); reading it back drops them."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in cls.model_computed_fields}
        return data


# --- Intake and review -------------------------------------------------------------------------


class TranscribedItem(Contract):
    """One item as read from the photo by the intake model."""

    name: str = Field(min_length=1)
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = None
    constraints: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    source_line: str | None = None


class TranscribedList(Contract):
    items: list[TranscribedItem]


class ConfirmedItem(TranscribedItem):
    """Same shape as TranscribedItem, after human review (ADR-0002)."""


class ConfirmedList(Contract):
    items: list[ConfirmedItem]


# --- Catalog -----------------------------------------------------------------------------------


class UnitOfSale(Contract):
    """How the store sells a product (ADR-0004)."""

    kind: Literal["unit", "pack", "weight_step"]
    step_size_g: float | None = Field(default=None, gt=0)
    pack_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _fields_match_kind(self) -> UnitOfSale:
        if self.kind == "weight_step" and self.step_size_g is None:
            raise ValueError("weight_step requires step_size_g")
        if self.kind != "weight_step" and self.step_size_g is not None:
            raise ValueError("step_size_g only applies to weight_step")
        if self.kind == "pack" and self.pack_size is None:
            raise ValueError("pack requires pack_size")
        if self.kind != "pack" and self.pack_size is not None:
            raise ValueError("pack_size only applies to pack")
        return self


class Candidate(Contract):
    id: str = Field(min_length=1)
    name: str
    brand: str | None = None
    size: str | None = None
    unit_of_sale: UnitOfSale
    price: Decimal | None = Field(default=None, ge=0)
    in_stock: bool = True
    url: str


# --- Resolver ----------------------------------------------------------------------------------


class TargetQuantity(Contract):
    """How much of an item is wanted, independent of any product (ADR-0010)."""

    id: str
    label: str
    amount: float = Field(gt=0)
    unit: Literal["unit", "g", "kg", "pack"]


class DecisionStatus(StrEnum):
    ADDED = "ADDED"
    ADDED_LOW_CONFIDENCE = "ADDED_LOW_CONFIDENCE"
    NOT_SURE = "NOT_SURE"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


class DecisionFlag(StrEnum):
    QUANTITY_ASSUMED = "QUANTITY_ASSUMED"  # no quantity on the list nor in preferences (ADR-0008)
    QUANTITY_INEXACT = "QUANTITY_INEXACT"  # target amount didn't convert exactly
    REDISCOVERY_NEEDED = "REDISCOVERY_NEEDED"  # execution failed because the site changed


class Alternative(Contract):
    candidate_id: str
    p: Probability = Field(ge=0, le=1)


class Decision(Contract):
    item: ConfirmedItem
    candidate_id: str | None = None
    target_quantity: TargetQuantity | None = None
    p_product: Probability | None = Field(default=None, ge=0, le=1)
    p_quantity: Probability | None = Field(default=None, ge=0, le=1)
    status: DecisionStatus
    flags: list[DecisionFlag] = Field(default_factory=list)
    rationale: str | None = None
    alternatives: list[Alternative] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> Probability | None:
        """min(p_product, p_quantity); p_product alone when no quantity question was asked."""
        if self.p_product is None:
            return None
        if self.p_quantity is None:
            return self.p_product
        return min(self.p_product, self.p_quantity)


# --- Executor ----------------------------------------------------------------------------------


class SaleQuantity(Contract):
    """A target amount converted into a product's unit of sale (executor output)."""

    candidate_id: str
    steps_or_units: int = Field(ge=1)
    effective_amount: float = Field(gt=0)
    effective_unit: Literal["unit", "g", "pack"]
    exact: bool


class CartLine(Contract):
    product_id: str
    quantity: int = Field(ge=1)
    verified: bool


# --- Report ------------------------------------------------------------------------------------


class ReportItem(Contract):
    decision: Decision
    candidate: Candidate | None = None  # the product picked (or the one that would have been)
    alternative_candidates: list[Candidate] = Field(default_factory=list)
    sale_quantity: SaleQuantity | None = None
    cart_line: CartLine | None = None


class RunReport(Contract):
    run_id: str
    items: list[ReportItem]
    dry_run: bool = False  # True when nothing was really added to a cart

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cart_total(self) -> Decimal | None:
        """Sum of price x quantity for lines that were added; None when a price is unknown."""
        total = Decimal(0)
        for item in self.items:
            if item.cart_line is None:
                continue
            if item.candidate is None or item.candidate.price is None:
                return None
            total += item.candidate.price * item.cart_line.quantity
        return total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def counts(self) -> dict[DecisionStatus, int]:
        counts = dict.fromkeys(DecisionStatus, 0)
        for item in self.items:
            counts[item.decision.status] += 1
        return counts
