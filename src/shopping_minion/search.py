"""Search pass (LLD sections 3.3, 7.1 and 7.7).

The page loads its own results as JSON. We only read the responses it receives; nothing is
sent by hand. A response is ours when its URL path ends with `/search` and its `search`
query parameter equals the term.
"""

import re
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import yaml
from playwright.sync_api import Page, Response

from shopping_minion.browser import BASE_URL, dismiss_cookie_banner, settle
from shopping_minion.history import norm
from shopping_minion.items import Candidate, Item

MAX_CANDIDATES = 15
WAIT_SECONDS = 10.0
NO_RESULTS_TEXT = re.compile(r"(?<!\d)0 itens")


class SearchError(RuntimeError):
    """The search page did not deliver its results in time."""


def search_url(term: str) -> str:
    return f"{BASE_URL}/busca/{quote(term, safe='')}"


def _money(value: float | int | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def candidate_from_hit(hit: dict) -> Candidate:
    """One hit of the search response, mapped as in LLD section 7.1."""
    pricing = hit.get("pricing") or {}
    quantity = hit.get("quantity") or {}
    unit = "kg" if hit["saleUnit"] == "KG" else "un"
    return Candidate(
        product_id=str(hit["id"]),
        slug=hit["slug"],
        name=hit["name"],
        brand=hit.get("brandName"),
        price=_money(pricing.get("promotionalPrice")),
        list_price=_money(pricing.get("price")) if pricing.get("promotion") else None,
        unit_of_sale=unit,
        step_kg=quantity.get("fraction") if unit == "kg" else None,
        min_kg=quantity.get("min") if unit == "kg" else None,
        available=(quantity.get("inStock") or 0) > 0,
        image=hit.get("image") or None,
    )


def candidates_from_response(body: dict) -> list[Candidate]:
    return [candidate_from_hit(hit) for hit in body.get("hits", [])]


def _query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def is_search_response(url: str, term: str) -> bool:
    """Path ends with `/search` and the `search` param is the term.

    Observed (T4): for a term with a space or an accent, the page puts the term in the
    param still percent-encoded (`papel%2520higienico` on the wire), because it passes the
    route segment on as is. So the param matches either as it is or decoded once more.
    """
    if not urlsplit(url).path.endswith("/search"):
        return False
    return any(
        value == term or unquote(value) == term for value in _query_of(url).get("search", [])
    )


def _offset(url: str) -> int:
    return int(_query_of(url).get("from", ["0"])[0])


SYNONYMS_PATH = "config/search_terms.yaml"


def load_search_terms(path: str | Path = SYNONYMS_PATH) -> dict[str, str]:
    """The synonym file (LLD-M5 section 3.2): a term to the term the store knows. Keys are
    normalized like `history.norm`; a missing file means no synonyms."""
    file = Path(path)
    if not file.is_file():
        return {}
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    return {norm(str(key)): str(value) for key, value in data.items()}


def apply_synonym(term: str, synonyms: dict[str, str]) -> str:
    """The synonym of the normalized term on a hit; the term as it is on a miss."""
    return synonyms.get(norm(term), term)


class SearchResults(list[Candidate]):
    """The candidates of one search. `retried` is True when the first read came back empty and
    the same URL was opened once more (LLD-M5 section 1.1); with alternatives, when any of the
    searches did. `terms` has the term of each search that ran, as (written, searched): they
    differ when a synonym replaced the written one. It is a list, so callers that only want the
    candidates don't change."""

    retried: bool = False
    terms: list[tuple[str, str]]

    def __init__(self, *args):
        super().__init__(*args)
        self.terms = []


def _read_once(page: Page, term: str) -> list[Candidate]:
    seen: dict[int, Response] = {}  # `from` offset -> response

    def on_response(response: Response) -> None:
        if is_search_response(response.url, term):
            seen[_offset(response.url)] = response

    page.on("response", on_response)
    try:
        page.goto(search_url(term))
        settle(page)
        return _collect(page, seen)
    finally:
        page.remove_listener("response", on_response)


def _search_term(page: Page, term: str) -> tuple[list[Candidate], bool]:
    """One term: the candidates and whether the empty first read was repeated."""
    candidates = _read_once(page, term)
    retried = not candidates
    if retried:
        candidates = _read_once(page, term)
    return candidates, retried


def search(page: Page, item: Item, synonyms: dict[str, str] | None = None) -> SearchResults:
    """Search the store for the item's term, then for each alternative, and return up to 15
    candidates: merged in that order, without repeating a `product_id`.

    Each term goes through the synonyms first (`None` loads config/search_terms.yaml). An empty
    result is read once more, the way a user reloads an empty page: runs 10 and 11 had empty
    searches whose terms found 12 to 15 products later.
    """
    if synonyms is None:
        synonyms = load_search_terms()
    results = SearchResults()
    merged: dict[str, Candidate] = {}
    searched: set[str] = set()
    for written in [item.search_term, *item.alternatives]:
        term = apply_synonym(written, synonyms)
        if not term.strip() or term in searched:
            continue
        searched.add(term)
        results.terms.append((written, term))
        candidates, retried = _search_term(page, term)
        results.retried = results.retried or retried
        for candidate in candidates:
            merged.setdefault(candidate.product_id, candidate)
    dismiss_cookie_banner(page)
    results.extend(list(merged.values())[:MAX_CANDIDATES])
    return results


def _collect(page: Page, seen: dict[int, Response]) -> list[Candidate]:
    deadline = time.monotonic() + WAIT_SECONDS
    first: dict | None = None
    next_from: int | None = None
    candidates: list[Candidate] = []
    while time.monotonic() < deadline:
        if first is None:
            if 0 in seen:
                first = seen[0].json()
                candidates = candidates_from_response(first)
                next_from = first.get("nextFrom") if first.get("hasNext") else None
            elif page.get_by_text(NO_RESULTS_TEXT).first.is_visible():
                return []  # "0 itens" on the page, no response needed
        if first is not None:
            if next_from is None or len(candidates) >= MAX_CANDIDATES:
                return candidates
            if next_from in seen:
                return candidates + candidates_from_response(seen[next_from].json())
        page.wait_for_timeout(100)
    if first is None:
        raise SearchError(f"no search results within {WAIT_SECONDS:.0f} s")
    return candidates  # second page did not arrive in time: keep the first page


def search_all(
    page: Page,
    items: list[Item],
    progress: Callable[[int, int, Item, list[Candidate]], None] | None = None,
) -> list[list[Candidate]]:
    """Search the items one after another; `progress(i, total, item, candidates)` after each."""
    results = []
    synonyms = load_search_terms()
    for i, item in enumerate(items, start=1):
        candidates = search(page, item, synonyms)
        results.append(candidates)
        if progress:
            progress(i, len(items), item, candidates)
    return results
