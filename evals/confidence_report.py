"""Catalogs every Jev answer saved in SQLite and how its confidence is spread. No model call.

    uv run python evals/confidence_report.py [--db data/shopping-minion.sqlite] [--runs 8,9]

Per answer: Jev's choice and confidence, and, when the run says so, the product the user
ended up with. Jev's original answer for an item that went to the picker is in the `pick` row
of `run_log` (from M3 on); an accepted item keeps it on its decision. Before M3 a pick
overwrote the confidence, so those answers are counted as lost. The catalog goes to
`data/evals/confidence-<timestamp>.csv` (personal, never committed).
"""

import argparse
import csv
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from shopping_minion.storage import Storage

PERCENTILES = (10, 25, 50, 75, 90)
BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 1.01))


@dataclass
class Answer:
    run: int
    index: int
    item: str
    candidates: int
    variant: str  # the decide config's `history`, or "pre-M4" before runs logged it
    status: str  # the decision's status at the end of the run
    jev_choice: str | None  # product id; None is `nenhum`
    confidence: float
    final: str | None  # product id in the final cart; None when there is none
    outcome: str  # hit | miss | no_final


def percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (the usual "linear" method)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def catalog_run(storage: Storage, run_id: int) -> tuple[list[Answer], int]:
    """The answers of one run, and how many Jev answers it lost (pre-M3 picks)."""
    decisions = storage.read_decisions(run_id)
    log = storage.read_log(run_id)
    picks = {row["data"]["index"]: row["data"] for row in log if row["kind"] == "pick"}
    removed = {
        row["data"]["line_id"]
        for row in log
        if row["kind"] == "cart_edit" and row["data"].get("remove")
    }
    config = next((row["data"] for row in log if row["kind"] == "decide"), None)
    variant = (config or {}).get("history", "pre-M4")
    answers, lost = [], 0
    for index, decision in enumerate(decisions):
        if not decision.candidates:
            continue  # never reached Jev
        pick = picks.get(index)
        if pick is not None and pick.get("jev_confidence") is not None:
            choice, confidence = pick.get("jev_choice"), pick["jev_confidence"]
        elif decision.status != "user_chosen" and decision.confidence is not None:
            choice, confidence = decision.choice, decision.confidence
        else:
            lost += 1  # a pick from before M3 overwrote Jev's answer
            continue
        final = None
        if decision.status in ("accepted", "user_chosen") and decision.choice not in removed:
            final = decision.choice
        if final is None:
            outcome = "miss" if decision.choice in removed else "no_final"
        else:
            outcome = "hit" if choice == final else "miss"
        answers.append(
            Answer(
                run=run_id,
                index=index,
                item=decision.item.name,
                candidates=len(decision.candidates),
                variant=variant,
                status=decision.status,
                jev_choice=choice,
                confidence=confidence,
                final=final,
                outcome=outcome,
            )
        )
    return answers, lost


def _fmt(value: float | None) -> str:
    return "  -  " if value is None else f"{value:.2f}"


def _row(label: str, values: list[float]) -> str:
    cells = " ".join(f"{_fmt(percentile(values, p)):>5}" for p in PERCENTILES)
    return f"{label:<26} {len(values):>4}  {cells}"


def report(answers: list[Answer], lost: int) -> str:
    out = [
        f"{len(answers)} Jev answers catalogued; {lost} lost (picks before M3 overwrote them)",
        "",
        f"{'group':<26} {'n':>4}  " + " ".join(f"{'P' + str(p):>5}" for p in PERCENTILES),
    ]
    out.append(_row("all", [a.confidence for a in answers]))
    for outcome in ("hit", "miss", "no_final"):
        out.append(
            _row(f"outcome {outcome}", [a.confidence for a in answers if a.outcome == outcome])
        )
    for choice in ("product", "nenhum"):
        chosen = [a for a in answers if (a.jev_choice is None) == (choice == "nenhum")]
        out.append(_row(f"Jev chose {choice}", [a.confidence for a in chosen]))
    for variant in sorted({a.variant for a in answers}):
        out.append(
            _row(f"variant {variant}", [a.confidence for a in answers if a.variant == variant])
        )
    for run in sorted({a.run for a in answers}):
        out.append(_row(f"run {run}", [a.confidence for a in answers if a.run == run]))
    out += ["", "confidence band       n   hits  misses  no_final  hit rate (of hit+miss)"]
    for low, high in BANDS:
        band = [a for a in answers if low <= a.confidence < high]
        hits = sum(a.outcome == "hit" for a in band)
        misses = sum(a.outcome == "miss" for a in band)
        rate = f"{hits / (hits + misses):.0%}" if hits + misses else "-"
        label = f"[{low:.1f}, {min(high, 1.0):.1f}{']' if high > 1 else ')'}"
        no_final = len(band) - hits - misses
        out.append(f"{label:<18} {len(band):>4} {hits:>6} {misses:>7} {no_final:>9}  {rate:>8}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/shopping-minion.sqlite")
    parser.add_argument("--runs", help="comma-separated run ids (default: every run)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    storage = Storage(args.db)
    try:
        if args.runs:
            run_ids = [int(r) for r in args.runs.split(",")]
        else:
            run_ids = sorted(r["run_id"] for r in storage.recent_runs(limit=1_000_000))
        answers, lost = [], 0
        for run_id in run_ids:
            run_answers, run_lost = catalog_run(storage, run_id)
            answers += run_answers
            lost += run_lost
    finally:
        storage.close()

    print(report(answers, lost))
    out = args.out or Path(f"data/evals/confidence-{datetime.now():%Y%m%d-%H%M%S}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Answer.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(a) for a in answers)
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
