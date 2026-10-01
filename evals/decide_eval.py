"""Runs the resolver cases through decide (LLD section 4). Calls Jev: it is a paid run.

    dotenvx run -- uv run python evals/decide_eval.py [--batch-size N] [--max-candidates N]

With no --batch-size it runs batch sizes 5 and 1 and prints both.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from shopping_minion.config import DecideConfig, load_decide_config
from shopping_minion.decide import decide, nothing_fit
from shopping_minion.items import Candidate, Decision, Item, Quantity

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class Case:
    id: str
    item: Item
    candidates: list[Candidate]
    accept: list[str]
    reject: list[str]
    expect_none: bool


def fold(text: str) -> str:
    """Lowercase and strip accents, so labels match regardless of either."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def convert_candidate(raw: dict) -> Candidate:
    """alfa0 candidate -> Candidate. `weight_step` is sold by kg; `unit` and `pack` by unit."""
    sale = raw["unit_of_sale"]
    by_weight = sale["kind"] == "weight_step"
    step_g = sale.get("step_size_g")
    return Candidate(
        product_id=str(raw["id"]),
        # the fixtures' `url` is a search URL, not a product page, so the slug comes from the name
        slug=slugify(raw["name"]),
        name=raw["name"],
        brand=raw.get("brand"),
        price=Decimal(raw["price"]) if raw.get("price") is not None else None,
        list_price=None,
        unit_of_sale="kg" if by_weight else "un",
        step_kg=step_g / 1000 if by_weight and step_g else None,
        available=bool(raw["in_stock"]),
    )


def convert_item(raw: dict) -> Item:
    quantity = None
    if raw.get("quantity") is not None:
        quantity = Quantity(value=raw["quantity"], unit=raw["unit"])
    return Item(
        source_line=raw.get("source_line", raw["name"]),
        name=raw["name"],
        search_term=raw.get("search_term", raw["name"]),
        constraints=raw.get("constraints", []),
        brand=raw.get("brand"),
        quantity=quantity,
    )


def load_cases(fixtures: Path = FIXTURES, max_candidates: int | None = None) -> list[Case]:
    data = yaml.safe_load((fixtures / "resolver-cases.yaml").read_text(encoding="utf-8"))
    cases = []
    for raw in data["cases"]:
        recorded = json.loads((fixtures / "candidates" / raw["candidates"]).read_text("utf-8"))
        candidates = [convert_candidate(c) for c in recorded["candidates"][:max_candidates]]
        cases.append(
            Case(
                id=raw["id"],
                item=convert_item(raw["item"]),
                candidates=candidates,
                accept=raw.get("accept", []),
                reject=raw.get("reject", []),
                expect_none=raw.get("expect_none", False),
            )
        )
    return cases


def choice_is_correct(case: Case, name: str | None) -> bool:
    """`name` is the chosen product's name, or None for `nenhum`."""
    if case.expect_none:
        return name is None
    if name is None:
        return False
    folded = fold(name)
    if case.accept and not any(re.search(fold(p), folded) for p in case.accept):
        return False
    return not any(re.search(fold(p), folded) for p in case.reject)


def chosen_name(decision: Decision) -> str | None:
    for candidate in decision.candidates:
        if candidate.product_id == decision.choice:
            return candidate.name
    return None


@dataclass
class Outcome:
    case: Case
    decision: Decision
    name: str | None
    raw_correct: bool
    # "ok": accepted and right, or expect_none and nothing was added. "ask": the user decides.
    # "WRONG": a wrong product would be added.
    verdict: str
    flag_nothing_fit: bool


def score(case: Case, decision: Decision, config: DecideConfig) -> Outcome:
    name = chosen_name(decision)
    raw_correct = choice_is_correct(case, name)
    if decision.status == "accepted":
        verdict = "ok" if raw_correct else "WRONG"
    elif case.expect_none:
        verdict = "ok"  # nothing is right, and nothing was added
    else:
        verdict = "ask"
    return Outcome(case, decision, name, raw_correct, verdict, nothing_fit(decision, config))


def totals(outcomes: list[Outcome]) -> dict[str, int]:
    return {
        "cases": len(outcomes),
        "correct after policy": sum(o.verdict == "ok" for o in outcomes),
        "asked (user decides)": sum(o.verdict == "ask" for o in outcomes),
        "asked, pick correct": sum(o.verdict == "ask" and o.raw_correct for o in outcomes),
        "raw picks correct": sum(o.raw_correct for o in outcomes),
        "wrong product would be added": sum(o.verdict == "WRONG" for o in outcomes),
    }


def run(config: DecideConfig, cases: list[Case], client) -> list[Outcome]:
    decisions = decide([(c.item, c.candidates) for c in cases], {}, config, client)
    return [score(c, d, config) for c, d in zip(cases, decisions, strict=True)]


def print_report(config: DecideConfig, outcomes: list[Outcome]) -> None:
    print(f"\n== batch_size={config.batch_size} model={config.model} ==")
    for o in outcomes:
        confidence = "-" if o.decision.confidence is None else f"{o.decision.confidence:.2f}"
        flag = " (nothing fit)" if o.flag_nothing_fit else ""
        print(
            f"{o.case.id:28} choice={o.name or 'nenhum'!r:50} conf={confidence:>5} "
            f"status={o.decision.status}{flag:14} raw={'yes' if o.raw_correct else 'NO ':3} "
            f"-> {o.verdict}"
        )
    for label, value in totals(outcomes).items():
        print(f"  {label}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-size", type=int, help="default: run 5 and 1")
    parser.add_argument("--max-candidates", type=int, help="default: all recorded (20)")
    args = parser.parse_args(argv)

    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY is not set; run under `dotenvx run --`.", file=sys.stderr)
        return 1

    from typesafe_sdk import TypeSafeClient

    base = load_decide_config(Path(__file__).parent.parent / "config" / "decide.yaml")
    cases = load_cases(max_candidates=args.max_candidates)
    sizes = [args.batch_size] if args.batch_size else [5, 1]
    with TypeSafeClient(model=base.model) as client:
        for size in sizes:
            config = base.model_copy(update={"batch_size": size})
            print_report(config, run(config, cases, client))
    return 0


if __name__ == "__main__":
    sys.exit(main())
