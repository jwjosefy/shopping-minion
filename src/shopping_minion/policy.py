"""Confidence policy: decision confidence -> status (ADR-0008, HLD §5)."""

from __future__ import annotations

from dataclasses import dataclass

from shopping_minion.contracts import DecisionStatus


@dataclass(frozen=True)
class Thresholds:
    """Placeholders until the evals calibrate them per backend (M6)."""

    high: float = 0.8
    skip: float = 0.5

    def __post_init__(self) -> None:
        if not 0 <= self.skip <= self.high <= 1:
            raise ValueError("thresholds must satisfy 0 <= skip <= high <= 1")


DEFAULT_THRESHOLDS = Thresholds()


def classify(
    confidence: float | None,
    *,
    needs_clarification: bool,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> DecisionStatus:
    if needs_clarification or confidence is None or confidence < thresholds.skip:
        return DecisionStatus.NOT_SURE
    if confidence < thresholds.high:
        return DecisionStatus.ADDED_LOW_CONFIDENCE
    return DecisionStatus.ADDED
