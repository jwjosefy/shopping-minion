"""Generic, deterministic catalog adapter: executes a site profile's search (ADR-0004, ADR-0006).

`fetch` performs the HTTP call through the browser context (same runtime as ADR-0007, so it also
works against a remote CDP browser). `parse_results` turns the payload into Candidates and is
pure, so recorded payloads double as offline regression fixtures.
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page

from shopping_minion.catalog.profile import Condition, HttpSearch, UnitRule, ValueSpec
from shopping_minion.contracts import Candidate, UnitOfSale

MAX_CANDIDATES = 20  # Julia-1's option limit (ADR-0010)

_MISSING = object()
_PATH_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]")


class ProfileError(ValueError):
    """The profile doesn't match what the site returned (a signal for rediscovery)."""


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


def _fill(value: Any, query: str, limit: int) -> Any:
    if isinstance(value, str):
        return value.replace("{query}", query).replace("{limit}", str(limit))
    if isinstance(value, dict):
        return {k: _fill(v, query, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, query, limit) for v in value]
    return value


_PAGE_FETCH_JS = """async ({url, method, headers, body}) => {
  const r = await fetch(url, {method, headers, body});
  return {status: r.status, ok: r.ok, text: await r.text()};
}"""
_pages: dict[int, Page] = {}  # one store page per browser context, kept for the whole run


async def _store_page(context: BrowserContext, page_url: str) -> Page:
    page = _pages.get(id(context))
    if page is None or page.is_closed():
        page = await context.new_page()
        await page.goto(page_url, wait_until="domcontentloaded")
        _pages[id(context)] = page
    return page


async def fetch(
    context: BrowserContext, spec: HttpSearch, query: str, limit: int | None = None
) -> Any:
    limit = limit or spec.page_size
    url = spec.url.replace("{query}", quote(query)).replace("{limit}", str(limit))
    headers = {"accept": "application/json", **spec.headers}
    body = json.dumps(_fill(spec.body, query, limit)) if spec.body is not None else None
    if body is not None:
        headers.setdefault("content-type", "application/json")

    if spec.transport == "page":
        assert spec.page_url is not None
        page = await _store_page(context, spec.page_url)
        result = await page.evaluate(
            _PAGE_FETCH_JS, {"url": url, "method": spec.method, "headers": headers, "body": body}
        )
        status, ok, text = result["status"], result["ok"], result["text"]
    else:
        kwargs: dict[str, Any] = {"method": spec.method, "headers": headers, "timeout": 20_000}
        if body is not None:
            kwargs["data"] = body
        response = await context.request.fetch(url, **kwargs)
        status, ok, text = response.status, response.ok, await response.text()

    if not ok:
        raise ProfileError(f"search returned HTTP {status}: {text[:300]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ProfileError(f"search response is not JSON: {text[:300]}") from e


def parse_results(spec: HttpSearch, payload: Any) -> list[Candidate]:
    items = get_path(payload, spec.results_path)
    if items is _MISSING or not isinstance(items, list):
        raise ProfileError(f"results_path {spec.results_path!r} is not a list in the response")
    if len(items) > spec.page_size:
        raise ProfileError(
            f"the API returned {len(items)} results for page_size {spec.page_size}: it isn't "
            "answering the search the profile describes (rediscovery needed)"
        )
    candidates = []
    for item in items:
        candidate = _to_candidate(spec, item)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _to_candidate(spec: HttpSearch, item: Any) -> Candidate | None:
    f = spec.fields
    product_id, name = get_path(item, f.id), get_path(item, f.name)
    if product_id in (_MISSING, None) or name in (_MISSING, None):
        return None
    name = str(name).strip()
    return Candidate(
        id=str(product_id),
        name=name,
        brand=_text(item, f.brand),
        size=_text(item, f.size),
        unit_of_sale=unit_of_sale(spec.unit_of_sale, item, name),
        price=_price(get_path(item, f.price) if f.price else _MISSING),
        in_stock=_holds(f.in_stock, item) if f.in_stock else True,
        url=_template(f.url, item),
    )


def unit_of_sale(rule: UnitRule, item: Any, name: str) -> UnitOfSale:
    if rule.weight_when and _holds(rule.weight_when, item):
        step = _number(rule.step_g, item, name) if rule.step_g else None
        if step is None or step <= 0:
            raise ProfileError(f"product {name!r} is sold by weight but step_g didn't resolve")
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
    if value in (_MISSING, None, ""):
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


def _template(template: str, item: Any) -> str:
    def replace(match: re.Match[str]) -> str:
        value = get_path(item, match[1])
        return "" if value is _MISSING else quote(str(value), safe="")

    return re.sub(r"\{([^}]+)\}", replace, template)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def rank(query: str, candidates: list[Candidate], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Pre-rank by token overlap with the query, in-stock first (HLD §4.4).

    Preferences (ADR-0003) join the score in M3, when the resolver consumes them.
    """
    query_tokens = set(_normalize(query).split())

    def score(candidate: Candidate) -> tuple[bool, float]:
        tokens = set(_normalize(f"{candidate.name} {candidate.brand or ''}").split())
        overlap = len(query_tokens & tokens) / len(query_tokens) if query_tokens else 0
        return (candidate.in_stock, overlap)

    return sorted(candidates, key=score, reverse=True)[:limit]
