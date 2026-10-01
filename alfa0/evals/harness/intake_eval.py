"""Score an intake transcription against a ground-truth fixture.

    dotenvx run -- uv run python evals/harness/intake_eval.py inbox/list.jpg evals/fixtures/list-001.yaml
    uv run python evals/harness/intake_eval.py --transcription data/evals/<file>.json evals/fixtures/list-001.yaml

Override the configured intake model to compare candidates without editing config/models.yaml:

    dotenvx run -- uv run python evals/harness/intake_eval.py inbox/list.jpg evals/fixtures/list-001.yaml \
        --provider openai --model z-ai/glm-5.3-flash \
        --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY

Names are matched loosely (accents and case ignored, similarity >= 0.75, or one name contained in
the other), because "filtro" vs. "filtro de café" is a wording difference, not a reading error.
Transcriptions are saved under data/evals/ (git-ignored) so they can be re-scored for free.
"""

from __future__ import annotations

import argparse
import mimetypes
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from shopping_minion.config import ChatRole, load_models_config
from shopping_minion.contracts import TranscribedItem, TranscribedList
from shopping_minion.intake import build_intake

OUT_DIR = Path("data/evals")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def similar(a: str, b: str) -> bool:
    a, b = normalize(a), normalize(b)
    return a == b or a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.75


@dataclass
class Expected:
    line: str
    tags: list[str]
    name: str
    quantity: float | None
    unit: str | None
    needs_clarification: bool


def load_fixture(path: Path) -> list[Expected]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected = []
    for line in data["lines"]:
        for item in line["items"]:
            quantity = item.get("quantity") or {}
            expected.append(
                Expected(
                    line=line["raw"],
                    tags=line.get("tags", []),
                    name=item["name"],
                    quantity=quantity.get("value"),
                    unit=quantity.get("unit"),
                    needs_clarification=item.get("needs_clarification", False),
                )
            )
    return expected


def match(expected: list[Expected], got: list[TranscribedItem]) -> list[TranscribedItem | None]:
    """Greedy, order-aware: each expected item takes the first unused similar transcribed item."""
    used: set[int] = set()
    matches: list[TranscribedItem | None] = []
    for exp in expected:
        found = None
        for i, item in enumerate(got):
            if i not in used and similar(exp.name, item.name):
                used.add(i)
                found = item
                break
        matches.append(found)
    return matches


def report(expected: list[Expected], got: TranscribedList) -> str:
    matches = match(expected, got.items)
    found = sum(m is not None for m in matches)
    extra = [
        item.name for item in got.items if not any(item is m for m in matches if m is not None)
    ]

    by_tag: dict[str, list[bool]] = defaultdict(list)
    lines: dict[str, list[bool]] = defaultdict(list)
    for exp, m in zip(expected, matches, strict=True):
        lines[exp.line].append(m is not None)
    for exp in expected:
        for tag in exp.tags or ["plain"]:
            by_tag[tag].append(all(lines[exp.line]))

    quantity_rows = [(e, m) for e, m in zip(expected, matches, strict=True) if e.quantity]
    quantity_ok = sum(
        1
        for e, m in quantity_rows
        if m and m.quantity == e.quantity and normalize(m.unit or "") == normalize(e.unit or "")
    )
    clarify_rows = [(e, m) for e, m in zip(expected, matches, strict=True) if e.needs_clarification]
    clarify_ok = sum(1 for _, m in clarify_rows if m and m.needs_clarification)
    false_clarify = [
        m.name
        for e, m in zip(expected, matches, strict=True)
        if m and m.needs_clarification and not e.needs_clarification
    ]

    out = [
        f"items found:        {found}/{len(expected)} ({found / len(expected):.0%})",
        f"extra items:        {len(extra)} {extra if extra else ''}",
        f"inline quantities:  {quantity_ok}/{len(quantity_rows)}",
        f"clarification set:  {clarify_ok}/{len(clarify_rows)}"
        + (f"  (also flagged: {false_clarify})" if false_clarify else ""),
        "",
        "lines fully read, by tag:",
    ]
    for tag in sorted(by_tag):
        results = by_tag[tag]
        out.append(f"  {tag:<24} {sum(results)}/{len(results)}")
    missing = [
        f"{e.name!r} (from {e.line!r})" for e, m in zip(expected, matches, strict=True) if m is None
    ]
    if missing:
        out += ["", "missing:"] + [f"  {m}" for m in missing]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("photo", nargs="?", type=Path)
    source.add_argument("--transcription", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    args = parser.parse_args()

    if args.transcription:
        got = TranscribedList.model_validate_json(args.transcription.read_text(encoding="utf-8"))
        label = str(args.transcription)
    else:
        role = load_models_config().intake
        overrides = {
            "provider": args.provider,
            "model": args.model,
            "base_url": args.base_url,
            "api_key_env": args.api_key_env,
        }
        if any(overrides.values()):
            role = ChatRole.model_validate(
                role.model_dump() | {k: v for k, v in overrides.items() if v}
            )
        media_type = mimetypes.guess_type(args.photo.name)[0] or "image/jpeg"
        got = build_intake(role).transcribe(args.photo.read_bytes(), media_type)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = f"{role.provider}-{role.model}".replace("/", "_")
        out = OUT_DIR / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{slug}.json"
        out.write_text(got.model_dump_json(indent=2), encoding="utf-8")
        label = f"{role.provider}:{role.model} -> {out}"

    print(label)
    print(report(load_fixture(args.fixture), got))


if __name__ == "__main__":
    main()
