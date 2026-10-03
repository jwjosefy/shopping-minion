"""Measures what the order history does to Jev's hit rate on a saved run (LLD-M4 section 12).

Replay (paid, about one Jev call per 5 items, per variant):

    dotenvx run -- uv run python evals/history_eval.py --run N [--variants none,options,list] \
[--alpha 0.5,1,2] [--preferences data/preferencias.yaml]

With --live-only there is no Jev call: Jev's original choices are read from the run's own
`decisions` and `pick` rows (the acceptance measure of T18).
"""

import argparse
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shopping_minion.config import DecideConfig, load_decide_config, load_history_config
from shopping_minion.decide import NONE_KEY, decide
from shopping_minion.history import ItemHistory, lines_for_item, near_misses
from shopping_minion.items import Candidate, Decision, Item
from shopping_minion.orders import OrderLine
from shopping_minion.preferences import read_yaml_preferences
from shopping_minion.storage import Storage

ROOT = Path(__file__).parent.parent
VARIANTS = ("none", "options", "list")
PRIOR_CAP = 3  # LLD-M4 section 4.4: min(orders, 3)


@dataclass
class Row:
    """One item of the run, with the answer the user ended up with."""

    index: int
    decision: Decision  # as saved: a pick has overwritten Jev's choice
    final: str | None  # product id in the final cart, None when there is none
    outcome: str  # "decided", or why there is no final product: skipped, no_match, removed
    history: ItemHistory

    @property
    def item(self) -> Item:
        return self.decision.item

    @property
    def candidates(self) -> list[Candidate]:
        return self.decision.candidates

    @property
    def has_history(self) -> bool:
        return bool(self.history.products)


@dataclass
class Pick:
    """Jev's top choice for one item: a product id, or None for `nenhum`."""

    choice: str | None
    accepted: bool  # the confidence cleared accept_at with a product chosen


def ground_truth(decisions: list[Decision], log: list[dict]) -> list[tuple[str | None, str]]:
    """(final product id, outcome) per decision, in order (LLD-M4 section 12, step 1)."""
    removed = {
        row["data"]["line_id"]
        for row in log
        if row["kind"] == "cart_edit" and row["data"].get("remove")
    }
    truth = []
    for decision in decisions:
        if decision.status in ("accepted", "user_chosen") and decision.choice is not None:
            if decision.choice in removed:
                truth.append((None, "removed"))
            else:
                truth.append((decision.choice, "decided"))
        elif decision.status == "skipped":
            truth.append((None, "skipped"))
        else:
            truth.append((None, "no_match"))  # also an `ask` the run never got to answer
    return truth


def build_rows(
    decisions: list[Decision], log: list[dict], lines: list[OrderLine], related_lines: int
) -> list[Row]:
    rows = []
    truth = ground_truth(decisions, log)
    for index, (decision, (final, outcome)) in enumerate(zip(decisions, truth, strict=True)):
        history = lines_for_item(decision.item, decision.candidates, lines, k=related_lines)
        rows.append(Row(index, decision, final, outcome, history))
    return rows


def live_picks(rows: list[Row], log: list[dict]) -> dict[int, Pick]:
    """Jev's original choice per item, with no call: `choice` of an accepted decision, else
    `jev_choice` from the item's `pick` row (the last one, if the item has several)."""
    pick_rows = {row["data"]["index"]: row["data"] for row in log if row["kind"] == "pick"}
    picks = {}
    for row in rows:
        if row.decision.status == "accepted":
            picks[row.index] = Pick(row.decision.choice, accepted=True)
        elif row.index in pick_rows:
            picks[row.index] = Pick(pick_rows[row.index]["jev_choice"], accepted=False)
    return picks


def reweight(
    probabilities: dict[str, float], orders: dict[str, int], alpha: float
) -> dict[str, float]:
    """p' proportional to p x (1 + alpha x min(orders, 3)), normalized. `nenhum` has no orders."""
    weighted = {
        key: p * (1 + alpha * min(orders.get(key, 0), PRIOR_CAP))
        for key, p in probabilities.items()
    }
    total = sum(weighted.values())
    return {key: w / total for key, w in weighted.items()} if total > 0 else dict(probabilities)


def prior_pick(row: Row, base: Decision, alpha: float, config: DecideConfig) -> Pick:
    """Variant C for one item, from the `none` decision. Ties go to the `none` choice, then to
    the search order, then to `nenhum`."""
    if not base.probabilities:
        return Pick(base.choice, accepted=base.status == "accepted")
    orders = {pid: h.orders for pid, h in row.history.products.items()}
    weighted = reweight(base.probabilities, orders, alpha)
    top = max(weighted.values())
    tied = {key for key, p in weighted.items() if math.isclose(p, top, rel_tol=1e-9)}
    base_key = NONE_KEY if base.choice is None else base.choice
    order = [c.product_id for c in row.candidates] + [NONE_KEY]
    winner = base_key if base_key in tied else next(key for key in order if key in tied)
    choice = None if winner == NONE_KEY else winner
    return Pick(choice, accepted=choice is not None and weighted[winner] >= config.accept_at)


def rate(hits: int, decided: int) -> dict:
    return {"hits": hits, "decided": decided, "rate": hits / decided if decided else None}


def product_name(row: Row, product_id: str | None) -> str:
    if product_id is None and row.outcome == "removed":
        return "(removido na revisão do carrinho)"
    if product_id is None:
        return NONE_KEY
    for candidate in row.candidates:
        if candidate.product_id == product_id:
            return candidate.name
    return product_id


def score(rows: list[Row], picks: dict[int, Pick]) -> dict:
    """The metrics of one variant. Items with a final product and a Jev choice count, and so
    do items whose line was removed in the cart review: those are misses (LLD-M4 section 1).
    The accepted and picker counts are over those same items."""
    hits = {True: 0, False: 0}  # by has_history
    decided = {True: 0, False: 0}
    accepted = accepted_misses = to_picker = 0
    misses = []
    for row in rows:
        pick = picks.get(row.index)
        if pick is None or (row.final is None and row.outcome != "removed"):
            continue
        hit = pick.choice == row.final
        decided[row.has_history] += 1
        hits[row.has_history] += hit
        if pick.accepted:
            accepted += 1
            accepted_misses += not hit
        else:
            to_picker += 1
        if not hit:
            misses.append(
                {
                    "item": row.item.name,
                    "jev_pick": product_name(row, pick.choice),
                    "final": product_name(row, row.final),
                    "accepted": pick.accepted,
                    "has_history": row.has_history,
                }
            )
    return {
        "with_history": rate(hits[True], decided[True]),
        "without_history": rate(hits[False], decided[False]),
        "overall": rate(hits[True] + hits[False], decided[True] + decided[False]),
        "accepted": accepted,
        "accepted_misses": accepted_misses,
        "picker": to_picker,
        "misses": misses,
    }


def run_summary(rows: list[Row], picks: dict[int, Pick]) -> dict:
    """What does not depend on the variant: the exclusions, the near misses, the history gap."""
    outcomes = Counter(row.outcome for row in rows)
    near = [
        {
            "item": row.item.name,
            "line": line.name,
            "candidate": candidate.name,
            "similarity": round(similarity, 2),
            "candidate_is_final": candidate.product_id == row.final,
        }
        for row in rows
        for line, candidate, similarity in near_misses(row.history, row.candidates)
    ]
    return {
        "items": len(rows),
        "with_candidates": sum(bool(row.candidates) for row in rows),
        "decided": outcomes["decided"],
        "excluded": {k: outcomes[k] for k in ("skipped", "no_match", "removed")},
        "no_jev_choice": sum(row.final is not None and row.index not in picks for row in rows),
        "decided_with_history": sum(row.has_history for row in rows if row.final is not None),
        "final_without_history_but_related": sum(
            row.final is not None
            and row.final not in row.history.products
            and bool(row.history.related)
            for row in rows
        ),
        "near_misses": near,
    }


def replay(
    rows: list[Row],
    base: DecideConfig,
    variants: list[str],
    alphas: list[float],
    client: Any,
    prefs: dict,
) -> dict[str, dict[int, Pick]]:
    """Picks per variant name (`none`, `options`, `list`, `prior a=<alpha>`), by item index.
    One decide() call per variant over the items with candidates; C reuses the `none` one."""
    asked = [row for row in rows if row.candidates]
    pairs = [(row.item, row.candidates) for row in asked]
    histories = [row.history for row in asked]
    result: dict[str, dict[int, Pick]] = {}
    none_decisions: list[Decision] | None = None
    for variant in variants:
        config = base.model_copy(update={"history": variant})
        decisions = decide(pairs, prefs, config, client, histories)
        result[variant] = {
            row.index: Pick(d.choice, accepted=d.status == "accepted")
            for row, d in zip(asked, decisions, strict=True)
        }
        if variant == "none":
            none_decisions = decisions
    if none_decisions is not None:
        for alpha in alphas:
            result[f"prior a={alpha:g}"] = {
                row.index: prior_pick(row, decision, alpha, base)
                for row, decision in zip(asked, none_decisions, strict=True)
            }
    return result


def pct(part: dict) -> str:
    if part["rate"] is None:
        return "-"
    return f"{part['hits']}/{part['decided']} {part['rate']:.0%}"


def print_report(summary: dict, results: dict[str, dict]) -> None:
    print(
        f"\n{summary['items']} items, {summary['decided']} decided "
        f"({summary['decided_with_history']} with history); excluded {summary['excluded']}; "
        f"without a Jev choice: {summary['no_jev_choice']}"
    )
    print(
        f"final product without history but a related line: "
        f"{summary['final_without_history_but_related']}; "
        f"near misses: {len(summary['near_misses'])}"
    )
    print(
        f"\n{'variant':14} {'with history':>13} {'without':>11} {'overall':>11} "
        f"{'accepted':>8} {'wrong':>5} {'picker':>6}"
    )
    for name, m in results.items():
        print(
            f"{name:14} {pct(m['with_history']):>13} {pct(m['without_history']):>11} "
            f"{pct(m['overall']):>11} {m['accepted']:>8} {m['accepted_misses']:>5} "
            f"{m['picker']:>6}"
        )
    for name, m in results.items():
        print(f"\nmisses, {name}:")
        for miss in m["misses"]:
            flag = " [accepted]" if miss["accepted"] else ""
            print(f"  {miss['item']}: Jev {miss['jev_pick']!r}, final {miss['final']!r}{flag}")
        if not m["misses"]:
            print("  none")


def load_run(db: Path, run_id: int, related_lines: int) -> tuple[list[Row], list[dict]]:
    storage = Storage(db)
    try:
        created = {r["id"]: r["created_at"] for r in storage.list_runs()}.get(run_id)
        if created is None:
            raise SystemExit(f"run {run_id} not found in {db}")
        decisions = storage.read_decisions(run_id)
        log = storage.read_log(run_id)
        before = datetime.fromisoformat(created)
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        lines = storage.order_lines(before=before)  # later orders hold the answers
    finally:
        storage.close()
    return build_rows(decisions, log, lines, related_lines), log


def parse_list(text: str, cast: Callable = str) -> list:
    return [cast(part) for part in text.split(",") if part.strip()]


def typesafe_client(model: str) -> Any:
    from typesafe_sdk import TypeSafeClient

    return TypeSafeClient(model=model)


def main(argv: list[str] | None = None, client_factory: Callable[[str], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--db", type=Path, default=Path("data/shopping-minion.sqlite"))
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--alpha", default="0.5,1,2")
    parser.add_argument("--preferences", type=Path, default=Path("data/preferencias.yaml"))
    parser.add_argument("--live-only", action="store_true", help="no Jev call")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    variants = parse_list(args.variants)
    alphas = parse_list(args.alpha, float)
    if bad := [v for v in variants if v not in VARIANTS]:
        parser.error(f"unknown variants: {bad}; choose from {list(VARIANTS)}")
    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1

    base = load_decide_config(ROOT / "config" / "decide.yaml")
    related_lines = load_history_config(ROOT / "config" / "history.yaml").related_lines
    rows, log = load_run(args.db, args.run, related_lines)

    if args.live_only:
        by_variant = {"live": live_picks(rows, log)}
    else:
        asked = sum(bool(row.candidates) for row in rows)
        calls = -(-asked // base.batch_size)
        print(
            f"Paid Jev calls: {len(variants)} variants ({', '.join(variants)}) x {asked} "
            f"questions = {len(variants) * asked} questions, about {len(variants) * calls} "
            f"calls (batch_size={base.batch_size}, model={base.model})."
        )
        if alphas and "none" not in variants:
            print("variant C needs `none` in --variants; skipping it.")
            alphas = []
        if client_factory is None:
            if not os.environ.get("TYPESAFE_API_KEY"):
                print("TYPESAFE_API_KEY is not set; run under `dotenvx run --`.", file=sys.stderr)
                return 1
            client_factory = typesafe_client
        prefs = read_yaml_preferences(args.preferences)
        with client_factory(base.model) as client:
            by_variant = replay(rows, base, variants, alphas, client, prefs)

    summary = run_summary(rows, next(iter(by_variant.values()), {}))
    results = {name: score(rows, picks) for name, picks in by_variant.items()}
    print_report(summary, results)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("data/evals") / f"history-run{args.run}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run": args.run,
        "mode": "live-only" if args.live_only else "replay",
        "decide_config": base.model_dump(),
        "related_lines": related_lines,
        "summary": summary,
        "variants": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
