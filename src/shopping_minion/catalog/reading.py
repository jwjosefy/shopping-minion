"""Reading what a store's page showed into `Candidate`s (LLD 2.3). Pure: no browser, no network.

Two inputs: the JSON body of a response the page received, and the page's HTML. Both end up as
one "item" (a dict) per product, mapped with the same `FieldMap` and `UnitRule` (ADR-0004).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from selectolax.parser import HTMLParser

from shopping_minion.catalog.mapping import (
    Condition,
    DomField,
    DomSource,
    FieldMap,
    ResponseSource,
    UnitRule,
    ValueSpec,
)
from shopping_minion.contracts import Candidate, UnitOfSale

_MISSING = object()
_PATH_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]")


class ReadingError(ValueError):
    """What was read doesn't have the structure the site profile expects (the site changed?)."""


def get_path(data: Any, path: str) -> Any:
    """Read `a.b[0].c` from nested dicts/lists; returns _MISSING when any step is absent."""
    current = data
    for token in _PATH_TOKEN.findall(path):
        if token.startswith("["):
            index = int(token[1:-1])
            if not isinstance(current, list) or index >= len(current):
                return _MISSING
            current = current[index]
        else:
            if not isinstance(current, dict) or token not in current:
                return _MISSING
            current = current[token]
    return current


def candidates_from_response(
    source: ResponseSource, payload: Any, *, base_url: str
) -> list[Candidate]:
    items = get_path(payload, source.items)
    if not isinstance(items, list):
        raise ReadingError(f"expected a list at {source.items!r} in the response, found none")
    return candidates_from_items(source.fields, source.unit_of_sale, items, base_url=base_url)


def candidates_from_items(
    fields: FieldMap, unit_of_sale: UnitRule, items: list[Any], *, base_url: str
) -> list[Candidate]:
    """Map a plain list of items (dicts) into Candidates; items without id or name are skipped."""
    return _map_items(items, fields, unit_of_sale, base_url)


def candidates_from_html(source: DomSource, html: str, *, base_url: str) -> list[Candidate]:
    items = [_extract(card, source.extract) for card in HTMLParser(html).css(source.item)]
    return _map_items(items, source.fields, source.unit_of_sale, base_url)


def _extract(card: Any, extract: dict[str, DomField]) -> dict[str, str]:
    item: dict[str, str] = {}
    for key, field in extract.items():
        node = card.css_first(field.selector)
        if node is None:
            continue
        if field.attr:
            value = node.attributes.get(field.attr)
            if value is not None:
                item[key] = value.strip()
        else:
            item[key] = node.text(strip=True)
    return item


def _map_items(
    items: list[Any], fields: FieldMap, rule: UnitRule, base_url: str
) -> list[Candidate]:
    candidates = []
    for item in items:
        candidate = _to_candidate(fields, rule, item, base_url)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _to_candidate(f: FieldMap, rule: UnitRule, item: Any, base_url: str) -> Candidate | None:
    product_id, name = get_path(item, f.id), get_path(item, f.name)
    if product_id in (_MISSING, None, "") or name in (_MISSING, None, ""):
        return None
    name = str(name).strip()
    return Candidate(
        id=str(product_id),
        name=name,
        brand=_text(item, f.brand),
        size=_text(item, f.size),
        unit_of_sale=unit_of_sale(rule, item, name),
        price=_price(get_path(item, f.price) if f.price else _MISSING),
        in_stock=_holds(f.in_stock, item) if f.in_stock else True,
        url=_template(f.url, item, base_url),
    )


def unit_of_sale(rule: UnitRule, item: Any, name: str) -> UnitOfSale:
    if rule.weight_when and _holds(rule.weight_when, item):
        step = _number(rule.step_g, item, name) if rule.step_g else None
        if step is None or step <= 0:
            raise ReadingError(
                f"expected step_g to resolve to a positive number for product {name!r}, "
                "which is sold by weight"
            )
        return UnitOfSale(kind="weight_step", step_size_g=step)
    pack = _number(rule.pack_size, item, name) if rule.pack_size else None
    if pack is not None and pack > 1:
        return UnitOfSale(kind="pack", pack_size=int(pack))
    return UnitOfSale(kind="unit")


def _holds(condition: Condition, item: Any) -> bool:
    value = get_path(item, condition.path)
    if value is _MISSING:
        return False
    if condition.equals is not None and value in condition.equals:
        return True
    if condition.matches is not None and re.search(condition.matches, str(value), re.IGNORECASE):
        return True
    return condition.equals is None and condition.matches is None and bool(value)


def _number(spec: ValueSpec, item: Any, name: str) -> float | None:
    raw: Any = _MISSING
    if spec.path == "name":
        raw = name
    elif spec.path:
        raw = get_path(item, spec.path)
    if raw not in (_MISSING, None):
        text = str(raw)
        if spec.regex:
            match = re.search(spec.regex, text, re.IGNORECASE)
            text = match.group(1) if match else ""
        try:
            return float(text.replace(",", ".")) * spec.scale
        except ValueError:
            pass
    return spec.constant


def _text(item: Any, path: str | None) -> str | None:
    if not path:
        return None
    value = get_path(item, path)
    return None if value in (_MISSING, None, "") else str(value).strip()


def _price(value: Any) -> Decimal | None:
    if value in (_MISSING, None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = re.sub(r"[^\d,.]", "", str(value))
    if "," in text:  # Brazilian format: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _template(template: str, item: Any, base_url: str) -> str:
    """Fill `{path}` placeholders (URL-quoted); `{base_url}` is used as it is."""

    def replace(match: re.Match[str]) -> str:
        if match[1] == "base_url":
            return base_url
        value = get_path(item, match[1])
        return "" if value is _MISSING else quote(str(value), safe="")

    url = re.sub(r"\{([^}]+)\}", replace, template)
    if url.startswith("/") and not url.startswith("//"):
        url = base_url.rstrip("/") + url
    return url
