# ADR-0005: The model decides, a deterministic executor acts

- **Status:** Accepted. Low-confidence clause superseded by [ADR-0008](0008-low-confidence-decisions-added-and-flagged.md); rationale made optional by [ADR-0010](0010-single-pass-typed-resolver.md); "never browses" scoped to shopping time by [ADR-0006](0006-discovery-agent-writes-site-profile.md).
- **Date:** 2026-09-29

## Context

The system has to modify a real shopping cart on a third-party website. The fuzzy part of the problem (which of 17 search results matches "café", and how many units make "1 kg") is well suited to an LLM. The part that touches the outside world (clicking steppers, adding items, respecting units of sale) is not: errors there cost money and are hard to spot.

The first prototype mixed the two in a single script. It worked on happy paths and was hard to trust or debug on anything else.

## Options considered

1. **Browser agent end-to-end.** Give the model browser control and let it search, choose, and add to cart.
   Rejected: non-deterministic in the step where mistakes are expensive, hard to evaluate, and slow.
2. **Pure deterministic matching.** Fuzzy string matching plus rules, no model.
   Rejected: breaks on the long tail (abbreviations, handwriting noise, "the usual one").
3. **Split decision from execution.** Deterministic retrieval → model returns a structured decision → deterministic executor validates and applies it.

## Decision

Option 3.

- The **catalog layer** searches the store and returns normalized candidates (id, name, brand, size, unit of sale, price, stock).
- The **resolver** receives the requested item, the preferences, and the candidates. It returns a structured decision: selected candidate, quantity in the product's own unit of sale, confidence, and a short rationale. It has no tools that write.
- The **executor** validates the decision (candidate exists, quantity within bounds and compatible with the unit of sale) and only then modifies the cart. Invalid or low-confidence decisions are flagged for human review instead of being applied.
- **Checkout is always manual.**

## Consequences

- Every model decision is a logged, inspectable artifact, which directly enables an evaluation harness.
- The executor becomes the one place where store-specific quirks (e.g., weight steppers) live.
- The model can be swapped without touching execution code.
- Cost: more upfront structure than a single script.
