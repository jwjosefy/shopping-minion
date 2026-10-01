"""Target quantity and conversion to stepper clicks (LLD section 3.5). Pure functions."""

import math
from decimal import Decimal
from typing import Literal

from shopping_minion.items import Item, Quantity
from shopping_minion.preferences import preference_quantity

Flag = Literal["QUANTITY_ASSUMED", "QUANTITY_INEXACT"]

_COUNT_UNITS = {"un", "pct", "cx", "lata"}
_WEIGHT_TO_KG = {"g": Decimal("0.001"), "kg": Decimal(1)}


def target_quantity(item: Item, pref_entry: dict | None) -> tuple[Quantity, list[Flag]]:
    """A: the quantity on the list. B: the preferences entry. C: 1 un, flagged."""
    if item.quantity is not None:
        return item.quantity, []
    from_prefs = preference_quantity(pref_entry)
    if from_prefs is not None:
        return from_prefs, []
    return Quantity(value=1, unit="un"), ["QUANTITY_ASSUMED"]


def to_clicks(
    target: Quantity, unit_of_sale: Literal["un", "kg"], step_kg: float | None
) -> tuple[int, list[Flag]]:
    """Convert the target into clicks on the product's stepper."""
    value = Decimal(str(target.value))

    if unit_of_sale == "un":
        if target.unit in _COUNT_UNITS or target.unit == "dz":
            count = value * 12 if target.unit == "dz" else value
            clicks = math.ceil(count)
            return clicks, [] if clicks == count else ["QUANTITY_INEXACT"]
        return 1, ["QUANTITY_INEXACT"]  # weight or volume on a unit product

    if target.unit in _WEIGHT_TO_KG:
        if step_kg is None or step_kg <= 0:
            raise ValueError("step_kg is required for a product sold by kg")
        ratio = value * _WEIGHT_TO_KG[target.unit] / Decimal(str(step_kg))
        clicks = math.ceil(ratio)
        return clicks, [] if clicks == ratio else ["QUANTITY_INEXACT"]
    return 1, ["QUANTITY_INEXACT"]  # count or volume on a kg product
