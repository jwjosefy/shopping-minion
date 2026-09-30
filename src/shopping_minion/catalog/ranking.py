"""Pre-ranking of candidates before the resolver sees them (HLD §4.4)."""

from __future__ import annotations

import unicodedata

from shopping_minion.contracts import Candidate

MAX_CANDIDATES = 20  # Julia-1's option limit (ADR-0010)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def rank(query: str, candidates: list[Candidate], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """In-stock first, then by share of the query's words found in the name or brand."""
    query_tokens = set(_normalize(query).split())

    def score(candidate: Candidate) -> tuple[bool, float]:
        tokens = set(_normalize(f"{candidate.name} {candidate.brand or ''}").split())
        overlap = len(query_tokens & tokens) / len(query_tokens) if query_tokens else 0
        return (candidate.in_stock, overlap)

    return sorted(candidates, key=score, reverse=True)[:limit]
