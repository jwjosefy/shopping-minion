# ADR-0013: The target quantity is derived by rule, not asked of the model

- **Status:** Accepted (drafted by Claude from Johann's rule, accepted by Johann on 2026-09-30)
- **Date:** 2026-09-30
- **Supersedes:** the quantity question of [ADR-0010](0010-single-pass-typed-resolver.md) ("two typed questions in one call"). The rest of ADR-0010 stands: one call per item, typed product question, pluggable backends, optional rationale.

## Context

ADR-0010 has the resolver ask the decision model two questions per item: which product, and which target quantity. When the resolver was built, the quantity question had nothing to decide: its options came from the quantity written on the list, and when nothing was written it wasn't asked. The code was written with a rule and no question, which left it out of step with ADR-0010 and HLD §4.5.

Johann's rule (2026-09-30):

- **A.** If the list gives a quantity, use it.
- **B.** If not, take it from the preferences file.
- **C.** If the preferences don't have it, assume 1 unit and flag the item for human review.

## Options considered

1. **Keep the quantity question** (ADR-0010 as written). A model call where a rule gives the same answer, plus a second probability that lowers confidence for no gain.
2. **Derive the quantity by rule (A, B, C).** Deterministic, testable, free.

## Decision

Option 2.

- The target quantity comes from A, B, C, in that order. C sets the `QUANTITY_ASSUMED` flag, which the report highlights whatever the confidence ([ADR-0008](0008-low-confidence-decisions-added-and-flagged.md)).
- The resolver asks the model one question: the product. A decision's confidence is the product probability.
- The executor converts the target quantity into the chosen product's unit of sale, as in ADR-0010, and flags inexact conversions (`QUANTITY_INEXACT`).
- Edge cases, already in the code:
  - a unit the system doesn't know ("2 latas") is counted as 2 units and flagged as assumed;
  - a weight asked of a product sold by unit, or a count asked of a product sold by weight, becomes 1 unit or 1 step, flagged as inexact.

## Consequences

- One model question per item instead of two.
- `p_quantity` stays in the `Decision` contract, unused, until something needs it.
- Step C will fire for most items while preferences are hand-written. Building preferences from the purchase history is the fix, and it is v1 ([journal 0008](../journal/2026-09-30-0008-purchase-history-is-the-fastest-preferences.md)).
- If a real list shows a written quantity that is ambiguous ("2" of what?), the question can come back as a new ADR.
