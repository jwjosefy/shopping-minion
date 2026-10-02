"""What the order history says about an item (LLD-M4 sections 4.3 and 11.1).

Pure functions, no model call: the same input always gives the same history, so the tests
can pin every case.
"""

import re
import unicodedata
from datetime import date

from shopping_minion.items import Candidate, Contract, Item, Quantity
from shopping_minion.orders import OrderLine

STOPWORDS = frozenset({"de", "da", "do", "das", "dos", "com", "e"})


class ProductHistory(Contract):
    product_id: str
    orders: int  # distinct orders with this product
    last_at: date  # the newest of them, local date
    last_quantity: Quantity  # unit "un" or "kg", from that order


class ItemHistory(Contract):
    products: dict[str, ProductHistory]  # step 1: only candidates with history, by product_id
    related: list[OrderLine]  # step 2: about the item, outside the candidates;
    # newest first, one per product, at most k


def norm(text: str) -> str:
    """Accents removed, case folded, anything but letters and digits turned into a space."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).split())


def words(text: str) -> set[str]:
    """The words of `norm(text)` without the stopwords. Numbers and units stay."""
    return {w for w in norm(text).split() if w not in STOPWORDS}


def local_date(line: OrderLine) -> date:
    """The order's date in this machine's local time (the line's `placed_at` is UTC)."""
    return line.placed_at.astimezone().date()


def _newest_first(lines: list[OrderLine]) -> list[OrderLine]:
    return sorted(lines, key=lambda line: line.placed_at, reverse=True)


def product_histories(lines: list[OrderLine]) -> dict[str, ProductHistory]:
    """One pass over the lines, newest first, grouped by product id."""
    order_ids: dict[str, set[str]] = {}
    last: dict[str, OrderLine] = {}
    for line in _newest_first(lines):
        order_ids.setdefault(line.product_id, set()).add(line.order_id)
        last.setdefault(line.product_id, line)  # the first seen is the newest
    return {
        product_id: ProductHistory(
            product_id=product_id,
            orders=len(order_ids[product_id]),
            last_at=local_date(newest),
            last_quantity=Quantity(value=newest.quantity, unit=newest.unit),
        )
        for product_id, newest in last.items()
    }


def lines_for_item(
    item: Item, candidates: list[Candidate], lines: list[OrderLine], k: int
) -> ItemHistory:
    """Step 1 by product id; step 2 by the words of the search term (LLD-M4 section 4.3)."""
    ids = {c.product_id for c in candidates}
    products = product_histories([line for line in lines if line.product_id in ids])
    wanted = words(item.search_term)
    related: list[OrderLine] = []
    if wanted:
        seen: set[str] = set()
        for line in _newest_first([line for line in lines if line.product_id not in ids]):
            if len(related) >= k:
                break
            if line.product_id in seen or not wanted <= words(line.name):
                continue
            seen.add(line.product_id)
            related.append(line)
    return ItemHistory(products=products, related=related)


def near_misses(
    history: ItemHistory, candidates: list[Candidate], threshold: float = 0.6
) -> list[tuple[OrderLine, Candidate, float]]:
    """Related lines that share at least `threshold` of their words (Jaccard) with a candidate:
    possible renames, for the eval only. One entry per line, with its best candidate."""
    found = []
    for line in history.related:
        line_words = words(line.name)
        best: tuple[Candidate, float] | None = None
        for candidate in candidates:
            cand_words = words(candidate.name)
            union = line_words | cand_words
            score = len(line_words & cand_words) / len(union) if union else 0.0
            if score >= threshold and (best is None or score > best[1]):
                best = (candidate, score)
        if best is not None:
            found.append((line, best[0], best[1]))
    return found
