"""Julia-1 decision backend: local, CPU, calibrated probabilities, no text output (ADR-0010).

Julia-1 (SupersonicLabs, Apache 2.0, 144M parameters) answers typed multiple-choice questions
with a probability per option. It takes 2 to 20 options and can't explain itself, so:
- candidates are capped at 19 and an explicit "none of these" option is added;
- `rationale` stays empty; the report shows the top alternatives with their probabilities.

The `julia` package is optional (it needs torch); it's imported when the backend is built.
Install: see README, "Julia-1 backend".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shopping_minion.contracts import Alternative, Candidate, ConfirmedItem
from shopping_minion.resolver import ProductChoice

MAX_OPTIONS = 20  # native limit of the model, including the "none" option
NONE_KEY = "none"

_engines: dict[tuple[str, str], Any] = {}


def _engine(path: str, device: str) -> Any:
    key = (path, device)
    if key not in _engines:
        try:
            from julia import load_model
        except ImportError as e:  # pragma: no cover - depends on the optional install
            raise RuntimeError(
                "the julia package isn't installed; see README, 'Julia-1 backend'"
            ) from e
        if not Path(path).exists():
            raise RuntimeError(
                f"Julia-1 weights not found at {path}; see README, 'Julia-1 backend'"
            )
        _engines[key] = load_model(
            path, device=device, strict_encoding=True, max_length=8192, head_length=512
        )
    return _engines[key]


def describe(candidate: Candidate) -> str:
    """One short line: the question and all options must fit in the model's 512-token head."""
    parts = [candidate.name]
    if candidate.brand and candidate.brand.lower() not in candidate.name.lower():
        parts.append(candidate.brand)
    if candidate.size:
        parts.append(candidate.size)
    if candidate.price is not None:
        parts.append(f"R$ {candidate.price}")
    if not candidate.in_stock:
        parts.append("OUT OF STOCK")
    return ", ".join(parts)


def build_state(item: ConfirmedItem, preference: dict[str, Any] | None) -> str:
    lines = [f"Item written on a Brazilian household's grocery list: {item.name}."]
    if item.source_line and item.source_line.lower() != item.name.lower():
        lines.append(f'As written on the paper: "{item.source_line}".')
    if item.constraints:
        lines.append("Constraints: " + "; ".join(item.constraints) + ".")
    if preference:
        lines.append("Known preferences: " + json.dumps(preference, ensure_ascii=False) + ".")
    lines.append("The customer wants the plain, standard version unless told otherwise.")
    return "\n".join(lines)


class Julia1Backend:
    def __init__(
        self, path: str, device: str = "cpu", name: str = "julia1", none_option: bool = True
    ) -> None:
        self.name = name
        self._path, self._device = path, device
        self._none_option = none_option

    def choose(
        self, item: ConfirmedItem, candidates: list[Candidate], preference: dict[str, Any] | None
    ) -> ProductChoice:
        engine = _engine(self._path, self._device)
        state = build_state(item, preference)
        count = min(len(candidates), MAX_OPTIONS - (1 if self._none_option else 0))
        while True:
            keys = {f"c{i}": c for i, c in enumerate(candidates[:count])}
            criteria = {key: describe(c) for key, c in keys.items()}
            if self._none_option:
                criteria[NONE_KEY] = "None of the listed products is the item the customer wrote"
            try:
                result = engine.predict(
                    state=state,
                    questions={
                        "product": {
                            "type": "choice",
                            "instructions": "Which product is the item written on the list?",
                            "criteria": criteria,
                        }
                    },
                )
                break
            except ValueError as e:
                # strict encoding refuses options that don't fit the 512-token head; candidates
                # are ranked, so dropping the tail loses the least relevant ones
                if "head budget" not in str(e) or count <= 2:
                    raise
                count -= 1
        answer = result["answers"]["product"]
        probabilities: dict[str, float] = answer["probabilities"]
        ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
        best_key, best_p = ranked[0]
        alternatives = [
            Alternative(candidate_id=keys[k].id, p=p) for k, p in ranked[1:4] if k in keys and p > 0
        ]
        if best_key == NONE_KEY:
            return ProductChoice(None, best_p, alternatives)
        return ProductChoice(keys[best_key].id, best_p, alternatives)
