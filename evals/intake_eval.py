"""OCR eval: run intake on a list photo and compare the result to a fixture (LLD section 4).

    uv run python evals/intake_eval.py --photo <photo> [--fixture evals/fixtures/list-001.yaml]
    uv run python evals/intake_eval.py --result lista.yaml   # score a saved `ocr` output, no call

The photo run calls `claude -p` once (Haiku, Johann's subscription: no API cost).

Rules, kept simple:

- Names are compared lowercase, without accents or extra spaces.
- **Items found / missed:** each fixture item is matched, by name alone, to one unused produced item
  (the ones from its own line first, then any). Unmatched fixture items are missed; produced items
  left over are extras.
- **Lines split wrongly:** produced items are grouped by runs of identical `source_line`. Each group
  is matched to the fixture line whose `raw` is most similar (difflib ratio >= 0.6, lines not
  claimed yet, ties to the earliest line, so duplicate lines are claimed in order). A fixture line
  is split wrongly when its group has a different number of items than the fixture expects, or when
  it has no group at all (line missing).
- **Constraints, brand, quantity:** checked on the found items only. Constraints are the fixture's
  `constraints` plus its `variant`, compared as a set to the produced `constraints` ("not x" in the
  fixture is read as "não x"). `brand` must be equal (both null counts as right). Quantity must
  have the same value and unit, or both be null.
- `needs_clarification` in the fixture is compared to `needs_review`, on the fixture-flagged items.
"""

import argparse
import difflib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from shopping_minion.intake import IntakeError, transcribe
from shopping_minion.items import Item

DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "list-001.yaml"
LINE_MATCH_MIN = 0.6


def norm(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def norm_constraint(text: str) -> str:
    value = norm(text)
    return "nao " + value[4:] if value.startswith("not ") else value


@dataclass
class LineResult:
    raw: str
    expected: int
    produced: int | None  # None: no produced group matched this line

    @property
    def split_ok(self) -> bool:
        return self.produced == self.expected


@dataclass
class Report:
    lines: list[LineResult] = field(default_factory=list)
    found: int = 0
    expected_items: int = 0
    missed: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    constraints_ok: int = 0
    brand_ok: int = 0
    quantity_ok: int = 0
    problems: list[str] = field(default_factory=list)
    review_expected: int = 0
    review_flagged: int = 0


def _groups(items: list[Item]) -> list[tuple[str, list[Item]]]:
    groups: list[tuple[str, list[Item]]] = []
    for item in items:
        key = norm(item.source_line)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(item)
        else:
            groups.append((key, [item]))
    return groups


def _quantity_ok(expected: dict | None, got: Item) -> bool:
    if expected is None or got.quantity is None:
        return expected is None and got.quantity is None
    return float(expected["value"]) == got.quantity.value and expected["unit"] == got.quantity.unit


def compare(fixture: dict, items: list[Item]) -> Report:
    report = Report()
    lines = fixture["lines"]

    # Match produced groups to fixture lines.
    claimed: dict[int, list[Item]] = {}
    for key, group in _groups(items):
        best, best_score = None, LINE_MATCH_MIN
        for idx, line in enumerate(lines):
            if idx in claimed:
                continue
            score = difflib.SequenceMatcher(None, key, norm(line["raw"])).ratio()
            if score > best_score:
                best, best_score = idx, score
        if best is not None:
            claimed[best] = group

    used: set[int] = set()  # ids of produced items already matched
    for idx, line in enumerate(lines):
        group = claimed.get(idx)
        report.lines.append(
            LineResult(line["raw"], len(line["items"]), None if group is None else len(group))
        )
        own = group or []
        pool = own + [i for i in items if not any(i is o for o in own)]
        for want in line["items"]:
            report.expected_items += 1
            got = next(
                (i for i in pool if id(i) not in used and norm(i.name) == norm(want["name"])), None
            )
            if want.get("needs_clarification"):
                report.review_expected += 1
                report.review_flagged += bool(got and got.needs_review)
            if got is None:
                report.missed.append(want["name"])
                continue
            used.add(id(got))
            report.found += 1
            expected_constraints = {norm_constraint(c) for c in want.get("constraints", [])}
            if want.get("variant"):
                expected_constraints.add(norm_constraint(want["variant"]))
            checks = {
                "constraints": expected_constraints
                == {norm_constraint(c) for c in got.constraints},
                "brand": norm(want.get("brand")) == norm(got.brand),
                "quantity": _quantity_ok(want.get("quantity"), got),
            }
            report.constraints_ok += checks["constraints"]
            report.brand_ok += checks["brand"]
            report.quantity_ok += checks["quantity"]
            for what, ok in checks.items():
                if not ok:
                    report.problems.append(
                        f"{want['name']}: {what} differs ({_describe(got, what)})"
                    )
    report.extras = [i.name for i in items if id(i) not in used]
    return report


def _describe(item: Item, what: str) -> str:
    if what == "constraints":
        return f"got {item.constraints}"
    if what == "brand":
        return f"got {item.brand!r}"
    return f"got {item.quantity.model_dump() if item.quantity else None}"


def print_report(report: Report) -> None:
    print(f"{'line':<34} {'expected':>8} {'got':>4}  split")
    for line in report.lines:
        got = "-" if line.produced is None else str(line.produced)
        flag = "ok" if line.split_ok else "WRONG"
        print(f"{line.raw[:34]:<34} {line.expected:>8} {got:>4}  {flag}")
    wrong = [line for line in report.lines if not line.split_ok]
    print()
    print(f"items found:        {report.found}/{report.expected_items}")
    print(f"items missed:       {len(report.missed)} {report.missed or ''}")
    print(f"extra items:        {len(report.extras)} {report.extras or ''}")
    print(f"lines split wrong:  {len(wrong)}/{len(report.lines)}")
    print(f"constraints right:  {report.constraints_ok}/{report.found}")
    print(f"brand right:        {report.brand_ok}/{report.found}")
    print(f"quantity right:     {report.quantity_ok}/{report.found}")
    print(f"needs_review on flagged items: {report.review_flagged}/{report.review_expected}")
    if report.problems:
        print("\ndifferences:")
        for problem in report.problems:
            print(f"  - {problem}")


def load_items(path: Path) -> list[Item]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Item.model_validate(raw) for raw in data["items"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--photo", type=Path, help="photo of the list (runs claude -p once)")
    source.add_argument("--result", type=Path, help="YAML written by `shopping-minion ocr`")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--model", default="haiku", help="claude model for --photo")
    parser.add_argument("--save", type=Path, help="also write the OCR result as YAML here")
    args = parser.parse_args(argv)

    fixture = yaml.safe_load(args.fixture.read_text(encoding="utf-8"))
    try:
        items = transcribe(args.photo, model=args.model) if args.photo else load_items(args.result)
    except IntakeError as exc:
        print(f"intake failed: {exc}", file=sys.stderr)
        return 1
    if args.save:
        dump = {"items": [item.model_dump(mode="json") for item in items]}
        args.save.write_text(yaml.safe_dump(dump, allow_unicode=True, sort_keys=False), "utf-8")
    print_report(compare(fixture, items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
