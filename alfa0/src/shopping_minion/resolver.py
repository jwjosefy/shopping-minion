"""Resolver: pick a product for one confirmed item (HLD §4.5, ADR-0005, ADR-0010).

The backend only answers the typed product question. The target quantity is derived
deterministically from the list and the preferences: the question would have no ambiguity to
resolve for the v0 items, so it isn't asked yet (recorded in docs/goal-run/).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from shopping_minion.config import ChatRole, ResolverRole
from shopping_minion.contracts import (
    Alternative,
    Candidate,
    ConfirmedItem,
    Decision,
    DecisionFlag,
    DecisionStatus,
    SaleQuantity,
    TargetQuantity,
)
from shopping_minion.executor_quantity import convert
from shopping_minion.models import chat_model
from shopping_minion.policy import DEFAULT_THRESHOLDS, Thresholds, classify
from shopping_minion.preferences import ItemPreference, Preferences

log = logging.getLogger(__name__)

_UNIT_ALIASES = {
    "g": "g",
    "gr": "g",
    "grama": "g",
    "gramas": "g",
    "kg": "kg",
    "quilo": "kg",
    "quilos": "kg",
    "un": "unit",
    "und": "unit",
    "unidade": "unit",
    "unidades": "unit",
    "pct": "unit",
    "pacote": "unit",
    "pacotes": "unit",
    "cx": "unit",
    "caixa": "unit",
    "caixas": "unit",
}


@dataclass(frozen=True)
class ProductChoice:
    candidate_id: str | None
    p_product: float | None
    alternatives: list[Alternative] = field(default_factory=list)
    rationale: str | None = None


class DecisionBackend(Protocol):
    name: str

    def choose(
        self, item: ConfirmedItem, candidates: list[Candidate], preference: dict[str, Any] | None
    ) -> ProductChoice: ...


# --- target quantity ---------------------------------------------------------------------------


def target_quantity(
    item: ConfirmedItem, preference: ItemPreference | None
) -> tuple[TargetQuantity, bool]:
    """(target, assumed). `assumed` is True when it isn't what the list says (ADR-0008)."""
    if item.quantity:
        unit = _UNIT_ALIASES.get((item.unit or "unit").lower().strip())
        if unit:
            return _target(item.quantity, unit), False
        return _target(item.quantity, "unit"), True  # unknown unit: count it, but flag it

    default = preference.default_quantity if preference else None
    if isinstance(default, int | float):
        return _target(float(default), "unit"), False
    if default is not None:
        unit = _UNIT_ALIASES.get(default.unit.lower(), "unit")
        return _target(default.value, unit), False
    return _target(1, "unit"), True


def _target(amount: float, unit: str) -> TargetQuantity:
    label = f"{amount:g} {unit}"
    return TargetQuantity(id=label.replace(" ", "-"), label=label, amount=amount, unit=unit)  # type: ignore[arg-type]


# --- resolution --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    decision: Decision
    sale_quantity: SaleQuantity | None


def resolve(
    item: ConfirmedItem,
    candidates: list[Candidate],
    preferences: Preferences,
    backend: DecisionBackend,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Resolution:
    if not candidates:
        return Resolution(Decision(item=item, status=DecisionStatus.NOT_FOUND), None)

    found = preferences.find(item.name)
    preference = found[1] if found else None
    target, assumed = target_quantity(item, preference)
    flags = [DecisionFlag.QUANTITY_ASSUMED] if assumed else []

    if item.needs_clarification:  # the human said so at review; don't spend a model call
        return Resolution(
            Decision(
                item=item, status=DecisionStatus.NOT_SURE, target_quantity=target, flags=flags
            ),
            None,
        )

    choice = backend.choose(item, candidates, preference.hints() if preference else None)
    chosen = next((c for c in candidates if c.id == choice.candidate_id), None)
    if chosen is None:  # model said "none of these", or named an id that isn't there
        return Resolution(
            Decision(
                item=item,
                status=DecisionStatus.NOT_SURE,
                target_quantity=target,
                p_product=choice.p_product,
                flags=flags,
                rationale=choice.rationale,
                alternatives=choice.alternatives,
            ),
            None,
        )

    sale = convert(target, chosen)
    if not sale.exact:
        flags.append(DecisionFlag.QUANTITY_INEXACT)
    status = classify(
        choice.p_product, needs_clarification=item.needs_clarification, thresholds=thresholds
    )
    decision = Decision(
        item=item,
        candidate_id=chosen.id,
        target_quantity=target,
        p_product=choice.p_product,
        status=status,
        flags=flags,
        rationale=choice.rationale,
        alternatives=choice.alternatives,
    )
    return Resolution(decision, sale if status != DecisionStatus.NOT_SURE else None)


# --- LLM backend (Claude Haiku 4.5 baseline, ADR-0010) -----------------------------------------

SYSTEM_PROMPT = """\
You choose which grocery product from a store's search results matches one item from a \
Brazilian household's shopping list. Items and products are in Portuguese.

Rules:
- Pick exactly one candidate `id`, or null if none of the candidates is what was asked for. \
Never invent an id.
- Respect the item's constraints ("preto não" excludes black beans; "normal" excludes \
flavored/light variants) and any preferences given (brand, variant, size, exclusions).
- Prefer in-stock products. Prefer the plain, standard version of a product when the list \
doesn't say otherwise; don't pick a premium or novelty variant on your own.
- `confidence` is the probability, from 0 to 1, that your pick is what the person meant. \
Be honest: use below 0.5 when you are guessing, and 0.9 or more only when it's clearly the one.
- List up to three other plausible candidates in `alternatives` with their own probabilities.
- `rationale`: one short sentence, in English.
"""


class _Alt(BaseModel):
    id: str
    confidence: float


class _Answer(BaseModel):
    chosen_id: str | None
    confidence: float
    alternatives: list[_Alt]
    rationale: str


class LLMBackend:
    def __init__(self, model: BaseChatModel, name: str = "llm") -> None:
        self.name = name
        self._structured = model.with_structured_output(_Answer, method="json_schema")

    def choose(
        self, item: ConfirmedItem, candidates: list[Candidate], preference: dict[str, Any] | None
    ) -> ProductChoice:
        request = {
            "item": {
                "name": item.name,
                "constraints": item.constraints,
                "written_on_list": item.source_line,
            },
            "preferences": preference,
            "candidates": [
                {
                    "id": c.id,
                    "name": c.name,
                    "brand": c.brand,
                    "size": c.size,
                    "price": str(c.price) if c.price is not None else None,
                    "sold_as": c.unit_of_sale.kind,
                    "in_stock": c.in_stock,
                }
                for c in candidates
            ],
        }
        answer = self._structured.invoke(
            [
                SystemMessage(SYSTEM_PROMPT),
                HumanMessage(json.dumps(request, ensure_ascii=False, indent=1)),
            ]
        )
        assert isinstance(answer, _Answer)
        known = {c.id for c in candidates}
        return ProductChoice(
            candidate_id=answer.chosen_id if answer.chosen_id in known else None,
            p_product=_clamp(answer.confidence),
            alternatives=[
                Alternative(candidate_id=a.id, p=_clamp(a.confidence))
                for a in answer.alternatives
                if a.id in known and a.id != answer.chosen_id
            ],
            rationale=answer.rationale.strip() or None,
        )


def _clamp(p: float) -> float:
    return min(1.0, max(0.0, p))


def build_backend(role: ChatRole, name: str) -> LLMBackend:
    return LLMBackend(chat_model(role), name)


def build_resolver_backend(role: ResolverRole) -> DecisionBackend:
    """The configured decision backend (ADR-0010): haiku (LLM), julia1 (local) or jev (later)."""
    if role.backend == "haiku":
        return build_backend(role.chat_role(), f"haiku:{role.model}")
    if role.backend == "julia1":
        from shopping_minion.resolver_julia import Julia1Backend

        assert role.path is not None
        return Julia1Backend(role.path, role.device, none_option=role.none_option)
    raise NotImplementedError("the Jev backend needs early access, which we don't have yet")
