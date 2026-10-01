"""Duplicate lines become one draft line (LLD-M2 section 6).

Seen on 2026-10-01: requeijão was on the list twice, both lines chose the same product, and
the cart got 1 where the list meant 2. Lines whose product has the same id merge into one:

- same unit kind (count or weight): the quantities are summed and converted to clicks once,
  so 250 g + 250 g on a 0.1 kg stepper is 5 clicks, not 3 + 3;
- otherwise (count + weight, or volume): the larger click count wins and the line is
  QUANTITY_INEXACT;
- flags are the union; the line keeps every source item.
"""

from decimal import Decimal

from shopping_minion.items import Candidate, CartTarget, DraftLine, Item, Quantity, estimate_price
from shopping_minion.quantity import Flag, to_clicks

_COUNT_TO_UN = {"un": Decimal(1), "pct": Decimal(1), "cx": Decimal(1), "lata": Decimal(1)}
_COUNT_TO_UN["dz"] = Decimal(12)
_WEIGHT_TO_KG = {"g": Decimal("0.001"), "kg": Decimal(1)}


def build_line(
    candidate: Candidate, items: list[Item], quantity: Quantity, assumed: bool = False
) -> DraftLine:
    """A draft line for `quantity` of the product. Raises ValueError for a kg product with no
    stepper increment (from `to_clicks`)."""
    clicks, inexact = to_clicks(quantity, candidate.unit_of_sale, candidate.step_kg)
    flags: list[Flag] = list(dict.fromkeys([*(["QUANTITY_ASSUMED"] if assumed else []), *inexact]))
    return DraftLine(
        line_id=candidate.product_id,
        candidate=candidate,
        items=items,
        quantity=quantity,
        target=CartTarget(product_id=candidate.product_id, clicks=clicks, flags=flags),
        flags=flags,
        estimated_price=estimate_price(candidate, clicks),
    )


def line_label(line: DraftLine) -> str:
    """The item names of a line: "requeijão", "requeijão ×2" or "a + b"."""
    names = [item.name for item in line.items]
    if len(names) == 1:
        return names[0]
    if len(set(names)) == 1:
        return f"{names[0]} ×{len(names)}"
    return " + ".join(names)


def _sum_quantities(quantities: list[Quantity]) -> Quantity | None:
    """The sum when all are count or all are weight (in un or kg); None otherwise."""
    if all(q.unit in _COUNT_TO_UN for q in quantities):
        total = sum((Decimal(str(q.value)) * _COUNT_TO_UN[q.unit] for q in quantities), Decimal(0))
        return Quantity(value=float(total), unit="un")
    if all(q.unit in _WEIGHT_TO_KG for q in quantities):
        total = sum((Decimal(str(q.value)) * _WEIGHT_TO_KG[q.unit] for q in quantities), Decimal(0))
        return Quantity(value=float(total), unit="kg")
    return None


def _merge_group(group: list[DraftLine]) -> DraftLine:
    if len(group) == 1:
        return group[0]
    candidate = group[0].candidate
    items = [item for line in group for item in line.items]
    assumed = any("QUANTITY_ASSUMED" in line.flags for line in group)
    total = _sum_quantities([line.quantity for line in group])
    if total is not None:
        return build_line(candidate, items, total, assumed)
    winner = max(group, key=lambda line: line.target.clicks)  # the first one on a tie
    flags: list[Flag] = list(
        dict.fromkeys([*(f for line in group for f in line.flags), "QUANTITY_INEXACT"])
    )
    return DraftLine(
        line_id=winner.line_id,
        candidate=candidate,
        items=items,
        quantity=winner.quantity,
        target=CartTarget(product_id=winner.line_id, clicks=winner.target.clicks, flags=flags),
        flags=flags,
        estimated_price=estimate_price(candidate, winner.target.clicks),
    )


def merge_lines(lines: list[DraftLine]) -> list[DraftLine]:
    """One line per product id, in the order each product first appears."""
    groups: dict[str, list[DraftLine]] = {}
    for line in lines:
        groups.setdefault(line.line_id, []).append(line)
    return [_merge_group(group) for group in groups.values()]
