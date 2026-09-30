"""Target amount -> product's unit of sale (HLD §4.6, ADR-0010).

Pure and unit-tested: unit arithmetic lives here and never in a model. Rounding rule: round to
the nearest whole unit/pack/step, half up, and never below 1. `exact` is False whenever the
converted amount differs from what was asked (or when the target can't be expressed in the
product's unit of sale, in which case 1 is used), so the report can show it.
"""

from __future__ import annotations

import math

from shopping_minion.contracts import Candidate, SaleQuantity, TargetQuantity

MAX_UNITS = 99  # sanity bound for one cart line


def _round_half_up(value: float) -> int:
    return max(1, math.floor(value + 0.5))


def to_grams(target: TargetQuantity) -> float | None:
    if target.unit == "g":
        return target.amount
    if target.unit == "kg":
        return target.amount * 1000
    return None


def convert(target: TargetQuantity, candidate: Candidate) -> SaleQuantity:
    unit_of_sale = candidate.unit_of_sale

    if unit_of_sale.kind == "weight_step":
        assert unit_of_sale.step_size_g is not None
        grams = to_grams(target)
        if grams is None:  # asked for "2 units" of something sold by weight: one step, flagged
            return SaleQuantity(
                candidate_id=candidate.id,
                steps_or_units=1,
                effective_amount=unit_of_sale.step_size_g,
                effective_unit="g",
                exact=False,
            )
        steps = min(_round_half_up(grams / unit_of_sale.step_size_g), MAX_UNITS)
        effective = steps * unit_of_sale.step_size_g
        return SaleQuantity(
            candidate_id=candidate.id,
            steps_or_units=steps,
            effective_amount=effective,
            effective_unit="g",
            exact=math.isclose(effective, grams),
        )

    if to_grams(target) is not None:  # grams asked for something sold by unit/pack: 1, flagged
        count, exact = 1, False
    else:
        count = min(_round_half_up(target.amount), MAX_UNITS)
        exact = math.isclose(count, target.amount)
    return SaleQuantity(
        candidate_id=candidate.id,
        steps_or_units=count,
        effective_amount=count,
        effective_unit="pack" if unit_of_sale.kind == "pack" else "unit",
        exact=exact,
    )
