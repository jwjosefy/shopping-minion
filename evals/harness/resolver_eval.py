"""Score a resolver backend against evals/fixtures/resolver-cases.yaml (ADR-0010, HLD §8).

    dotenvx run -- uv run python evals/harness/resolver_eval.py
    dotenvx run -- uv run python evals/harness/resolver_eval.py --model z-ai/glm-5.3-flash

Uses the resolver role from config/models.yaml, with the same command-line overrides as
intake_eval.py. Candidates are recorded lists in evals/fixtures/candidates/, so the store isn't
touched.
Reports accuracy, calibration (confidence when right vs. wrong), latency and the final status.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import unicodedata
from pathlib import Path

import yaml

from shopping_minion.catalog.ranking import rank
from shopping_minion.config import ResolverRole, load_models_config
from shopping_minion.contracts import Candidate, ConfirmedItem, DecisionStatus
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import build_resolver_backend, resolve

CASES = Path("evals/fixtures/resolver-cases.yaml")
CANDIDATES = Path("evals/fixtures/candidates")
OUT_DIR = Path("data/evals")


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def is_correct(case: dict, chosen_name: str | None) -> bool:
    if case.get("expect_none"):
        return chosen_name is None
    if chosen_name is None:
        return False
    name = norm(chosen_name)
    accepted = any(re.search(p, name) for p in case.get("accept", []))
    rejected = any(re.search(p, name) for p in case.get("reject", []))
    return accepted and not rejected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend", choices=["haiku", "julia1"], help="default: config/models.yaml"
    )
    parser.add_argument("--path", help="julia1: directory with the weights")
    parser.add_argument("--no-none-option", action="store_true", help="julia1: no 'none of these'")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    args = parser.parse_args()

    resolver_role = load_models_config().resolver
    overrides = {
        "backend": args.backend,
        "path": args.path,
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
    }
    merged = resolver_role.model_dump() | {k: v for k, v in overrides.items() if v}
    if args.no_none_option:
        merged["none_option"] = False
    if merged["backend"] == "julia1" and not merged.get("path"):
        merged["path"] = "data/models/Julia-1"
    resolver_role = ResolverRole.model_validate(merged)
    backend = build_resolver_backend(resolver_role)
    label = (
        f"julia1:{resolver_role.path}{'' if resolver_role.none_option else ' (no none option)'}"
        if resolver_role.backend == "julia1"
        else f"{resolver_role.provider}:{resolver_role.model}"
    )

    spec = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    preferences = Preferences({})  # repeatable: no personal preferences in evals

    rows = []
    for case in spec["cases"]:
        recorded = json.loads((CANDIDATES / case["candidates"]).read_text(encoding="utf-8"))
        item = ConfirmedItem.model_validate(case["item"])
        candidates = rank(item.name, [Candidate.model_validate(c) for c in recorded["candidates"]])
        started = time.perf_counter()
        resolution = resolve(item, candidates, preferences, backend)
        seconds = time.perf_counter() - started

        decision = resolution.decision
        by_id = {c.id: c for c in candidates}
        chosen = by_id.get(decision.candidate_id) if decision.candidate_id else None
        # "chosen" counts as none when the pipeline wouldn't add it (NOT_SURE / NOT_FOUND)
        effective = (
            chosen.name
            if chosen
            and decision.status in (DecisionStatus.ADDED, DecisionStatus.ADDED_LOW_CONFIDENCE)
            else None
        )
        rows.append(
            {
                "id": case["id"],
                "tags": case.get("tags", []),
                "chosen": chosen.name if chosen else None,
                "effective": effective,
                "confidence": decision.confidence,
                "status": decision.status.value,
                "correct": is_correct(case, effective),
                "raw_correct": is_correct(case, chosen.name if chosen else None),
                "would_add_wrong": bool(effective) and not is_correct(case, effective),
                "seconds": seconds,
                "rationale": decision.rationale,
                "sale": resolution.sale_quantity.model_dump() if resolution.sale_quantity else None,
            }
        )

    print(f"backend: {label}\n")
    for r in rows:
        mark = "OK " if r["correct"] else "BAD"
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else " - "
        print(
            f"{mark} {r['id']:<26} {r['status']:<22} conf {conf}  {r['seconds']:.1f}s  -> {r['chosen']}"
        )
        if r["sale"]:
            sale = r["sale"]
            print(
                f"      qty: {sale['steps_or_units']} x ({sale['effective_amount']:g} {sale['effective_unit']} total, exact={sale['exact']})"
            )
        if not r["correct"] and r["rationale"]:
            print(f"      why: {r['rationale']}")

    right = [r for r in rows if r["correct"]]
    wrong_added = [r for r in rows if r["would_add_wrong"]]
    raw = [r for r in rows if r["raw_correct"]]
    print(f"\ncorrect:               {len(right)}/{len(rows)}  (after the confidence policy)")
    print(f"raw picks correct:     {len(raw)}/{len(rows)}  (ignoring thresholds)")
    print(f"wrong item in the cart: {len(wrong_added)}  {[r['id'] for r in wrong_added]}")
    confs_right = [r["confidence"] for r in right if r["confidence"] is not None]
    confs_wrong = [
        r["confidence"] for r in rows if not r["correct"] and r["confidence"] is not None
    ]
    if confs_right:
        print(f"mean confidence right: {statistics.mean(confs_right):.2f}")
    if confs_wrong:
        print(f"mean confidence wrong: {statistics.mean(confs_wrong):.2f}")
    print(f"mean latency:          {statistics.mean(r['seconds'] for r in rows):.1f}s per item")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = label.replace("/", "_").replace(":", "-")
    out = OUT_DIR / f"resolver-{time.strftime('%Y%m%d-%H%M%S')}-{slug}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
