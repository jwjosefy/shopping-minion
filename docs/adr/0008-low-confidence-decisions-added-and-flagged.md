# ADR-0008: Low-confidence decisions are added and flagged; very low confidence is skipped

- **Status:** Accepted
- **Date:** 2026-09-29
- **Supersedes:** the low-confidence clause of [ADR-0005](0005-model-decides-executor-acts.md) ("invalid or low-confidence decisions are flagged for human review instead of being applied"). The rest of ADR-0005 stands.

## Context

ADR-0005 sends low-confidence decisions to human review before they touch the cart. That needs a review step between deciding and acting. v0 deliberately has no such step: after the transcribed list is confirmed ([ADR-0002](0002-human-review-of-transcribed-list.md)), the run goes all the way to the cart and returns a report. Reviewing or refining the model's choices is v1.

The user reviews the cart before checkout anyway, because checkout is always manual.

## Options considered

1. **Keep ADR-0005 as written:** low-confidence items are not added and wait for review. This needs a review UI that v0 doesn't have. Without that UI, these items are just silently left out.
2. **Add everything, and show confidence in the report.** Simple, but items the system is barely sure about end up in the cart.
3. **Two thresholds:** add and flag in the middle band, skip at the bottom.

## Decision

Option 3. The decision's confidence is `min(p_product, p_quantity)`, or `p_product` when no quantity question was asked.

| Confidence | Status | Cart |
|---|---|---|
| ≥ `high` | `ADDED` | added |
| ≥ `skip` and < `high` | `ADDED_LOW_CONFIDENCE` | added, flagged as a risk in the report |
| < `skip`, or `needs_clarification` | `NOT_SURE` | skipped, shown in the report |

- **Assumed quantity:** when the quantity had to be assumed (no quantity on the list and no `default_quantity` in the preferences), the item also gets a `QUANTITY_ASSUMED` flag. The flag applies whatever the confidence.
- **Thresholds:** configurable, and set from eval results.

## Consequences

- A v0 run always ends in a usable cart plus a report that points at what to double-check.
- The `NOT_SURE` band is where a clarification step goes in v1: LangGraph interrupts, asking the user the way a coding agent asks a question in the terminal.
- Wrong items in the flagged band cost the user a look at the cart, which they're doing anyway before checkout.
- This relies on confidence being meaningful. That's one more reason the evals measure calibration per backend ([ADR-0010](0010-single-pass-typed-resolver.md)).
