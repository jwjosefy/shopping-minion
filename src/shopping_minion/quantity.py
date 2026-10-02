"""Target quantity and conversion to stepper clicks (LLD section 3.5). Pure functions."""

import math
from decimal import Decimal
from typing import Literal

from shopping_minion.history import ProductHistory
from shopping_minion.items import Item, Quantity
from shopping_minion.preferences import preference_quantity

Flag = Literal["QUANTITY_ASSUMED", "QUANTITY_INEXACT", "QUANTITY_FROM_HISTORY"]
Rounding = Literal["up", "nearest"]

_COUNT_UNITS = {"un", "pct", "cx", "lata"}
_WEIGHT_TO_KG = {"g": Decimal("0.001"), "kg": Decimal(1)}


def target_quantity(
    item: Item,
    pref_entry: dict | None,
    history: ProductHistory | None = None,
    unit_of_sale: Literal["un", "kg"] | None = None,
) -> tuple[Quantity, list[Flag]]:
    """A: the quantity on the list. B: the preferences entry. C: the last quantity bought of
    the chosen product, flagged, when its unit is the product's unit of sale. D: 1 un, flagged."""
    if item.quantity is not None:
        return item.quantity, []
    from_prefs = preference_quantity(pref_entry)
    if from_prefs is not None:
        return from_prefs, []
    if history is not None and history.last_quantity.unit == unit_of_sale:
        return history.last_quantity, ["QUANTITY_FROM_HISTORY"]
    return Quantity(value=1, unit="un"), ["QUANTITY_ASSUMED"]


def _count(ratio: Decimal, rounding: Rounding) -> tuple[int, bool]:
    """Whole clicks for `ratio` of a step, and whether to call it exact. Nearest rounds half up
    and never gives 0; its inexactness is expected, so it always counts as exact."""
    if rounding == "nearest":
        return max(1, math.floor(ratio + Decimal("0.5"))), True
    clicks = math.ceil(ratio)
    return clicks, clicks == ratio


def to_clicks(
    target: Quantity,
    unit_of_sale: Literal["un", "kg"],
    step_kg: float | None,
    rounding: Rounding = "up",
) -> tuple[int, list[Flag]]:
    """Convert the target into clicks on the product's stepper. `up` is for an amount written
    on the list; `nearest` is for an amount taken from history (at least 1 click, not flagged)."""
    value = Decimal(str(target.value))

    if unit_of_sale == "un":
        if target.unit in _COUNT_UNITS or target.unit == "dz":
            count = value * 12 if target.unit == "dz" else value
            clicks, exact = _count(count, rounding)
            return clicks, [] if exact else ["QUANTITY_INEXACT"]
        return 1, ["QUANTITY_INEXACT"]  # weight or volume on a unit product

    if target.unit in _WEIGHT_TO_KG:
        if step_kg is None or step_kg <= 0:
            raise ValueError("step_kg is required for a product sold by kg")
        ratio = value * _WEIGHT_TO_KG[target.unit] / Decimal(str(step_kg))
        clicks, exact = _count(ratio, rounding)
        return clicks, [] if exact else ["QUANTITY_INEXACT"]
    return 1, ["QUANTITY_INEXACT"]  # count or volume on a kg product
