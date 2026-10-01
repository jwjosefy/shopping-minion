"""Check the final cart against the plan, deterministically (Johann, 2026-10-01).

The cart is read twice from the reloaded site: before the cart pass and at the end. Each
planned product is looked up in the final cart by name and its quantity compared as a number
("300g" = 0.3 kg). Products in the cart that aren't in the plan are listed apart, so a
leftover from an earlier run is visible instead of silently mixed in.
"""

import re
from dataclasses import dataclass

from shopping_minion.items import Candidate, CartTarget

CartLines = list[tuple[str, str]]  # (product name, quantity text) as the drawer shows them

_AMOUNT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(g|kg|un)?\s*$", re.IGNORECASE)


def normalize(name: str) -> str:
    return " ".join(name.lower().split())


def cart_amount(text: str) -> tuple[float, str] | None:
    """'3' -> (3, 'un'); '300g' -> (0.3, 'kg'); '1kg' -> (1, 'kg'). None if unreadable."""
    match = _AMOUNT.match(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "un").lower()
    if unit == "g":
        return round(value / 1000, 3), "kg"
    return value, unit


def expected_amount(candidate: Candidate, target: CartTarget) -> tuple[float, str]:
    if candidate.unit_of_sale == "kg":
        return round(target.clicks * (candidate.step_kg or 0), 3), "kg"
    return float(target.clicks), "un"


def format_amount(amount: tuple[float, str]) -> str:
    value, unit = amount
    if unit == "kg":
        return f"{value * 1000:g}g" if value < 1 else f"{value:g}kg"
    return f"{value:g}"


@dataclass
class Check:
    item_name: str
    product_name: str
    expected: str
    found: str | None  # quantity text in the final cart, None if the product isn't there
    was_before: bool  # the product was already in the cart before this run
    ok: bool
    verdict: str


def _index(lines: CartLines | None) -> dict[str, str]:
    return {normalize(name): quantity for name, quantity in lines or []}


def reconcile(
    planned: list[tuple[str, Candidate, CartTarget]],
    before: CartLines | None,
    after: CartLines,
) -> tuple[list[Check], CartLines]:
    """Return one Check per planned product, and the final cart lines that aren't planned."""
    before_index, after_index = _index(before), _index(after)
    checks = []
    for item_name, candidate, target in planned:
        key = normalize(candidate.name)
        expected = expected_amount(candidate, target)
        found = after_index.get(key)
        was_before = key in before_index
        if found is None:
            ok, verdict = False, "FALTANDO no carrinho"
        elif cart_amount(found) == expected:
            ok, verdict = True, "ok"
        else:
            ok = False
            verdict = (
                f"QUANTIDADE DIFERENTE: carrinho tem {found}, esperado {format_amount(expected)}"
            )
        if was_before:
            verdict += f" (já estava no carrinho antes desta rodada, com {before_index[key]})"
        checks.append(
            Check(
                item_name, candidate.name, format_amount(expected), found, was_before, ok, verdict
            )
        )
    planned_keys = {normalize(c.name) for _, c, _ in planned}
    extras = [(name, q) for name, q in after if normalize(name) not in planned_keys]
    return checks, extras


def report_lines(checks: list[Check], extras: CartLines, before: CartLines | None) -> list[str]:
    before_index = _index(before)
    out = ["Conferência do carrinho (lido do site depois de recarregar):"]
    for c in checks:
        mark = "ok " if c.ok else "!! "
        out.append(f"  {mark}{c.item_name} | {c.product_name} | esperado {c.expected}: {c.verdict}")
    good = sum(c.ok for c in checks)
    out.append(f"  {good} de {len(checks)} itens da lista conferem.")
    if extras:
        out.append(f"!! {len(extras)} produto(s) no carrinho que NÃO são desta lista:")
        for name, quantity in extras:
            note = " (já estava antes desta rodada)" if normalize(name) in before_index else ""
            out.append(f"     {quantity}  {name}{note}")
    if before is None:
        out.append(
            "  (o carrinho não pôde ser lido antes da rodada; 'já estava' não foi verificado)"
        )
    return out
