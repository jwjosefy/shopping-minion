"""Shared data models: how raw items read from a store's page map to `Candidate` fields.

Used by the site profile (which holds them) and by the reading code (which applies them). Data
only, no logic. "Item" below means one product as read from the page: a JSON object from a
response the page received, or a flat dict of strings extracted from the DOM.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Model):
    """True when the value at `path` equals one of `equals`, or matches the regex `matches`.

    With neither `equals` nor `matches`, true when the value is truthy (e.g. a stock count).
    """

    path: str
    equals: list[str | int | float | bool] | None = None
    matches: str | None = None


class ValueSpec(_Model):
    """A number read from `path` (optionally via `regex`, first group) and multiplied by `scale`.

    `path: name` with a regex reads from the product name, e.g. pack size from "12 rolos".
    `constant` is used when the path is absent or doesn't resolve.
    """

    path: str | None = None
    regex: str | None = None
    scale: float = 1.0
    constant: float | None = None


class UnitRule(_Model):
    """How to derive a product's unit of sale (ADR-0004) from an item."""

    weight_when: Condition | None = None
    step_g: ValueSpec | None = None
    pack_size: ValueSpec | None = None


class FieldMap(_Model):
    """Where each Candidate field lives inside one item (dotted paths, `[n]` for lists).

    `url` is a template whose `{...}` placeholders are paths in the item; their values are
    URL-quoted. It may only point to a page of the store.
    """

    id: str
    name: str
    url: str
    brand: str | None = None
    size: str | None = None
    price: str | None = None
    in_stock: Condition | None = None


class DomField(_Model):
    """One value read from an element inside a product card: its text, or an attribute."""

    selector: str
    attr: str | None = None


class ResponseSource(_Model):
    """Results read from a response the page itself received (ADR-0012: observed, never sent)."""

    url_matches: str  # regex on the response URL's path; a pattern to recognise it, never a URL to call
    items: str  # dotted path to the list of items in the JSON body
    fields: FieldMap
    unit_of_sale: UnitRule = UnitRule()


class DomSource(_Model):
    """Results read from the page's DOM: each card becomes a flat dict, then mapped like JSON."""

    item: str  # CSS selector of one product card
    extract: dict[str, DomField]  # name -> where to read it inside the card
    fields: FieldMap  # paths are the names in `extract`
    unit_of_sale: UnitRule = UnitRule()
