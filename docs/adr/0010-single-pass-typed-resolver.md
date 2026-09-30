# ADR-0010: The resolver asks typed multiple-choice questions in a single call; backend chosen by evals

- **Status:** Accepted. The quantity question was superseded by [ADR-0013](0013-target-quantity-is-derived-not-asked.md): the target quantity is derived by rule.
- **Date:** 2026-09-29
- **Amends:** [ADR-0005](0005-model-decides-executor-acts.md)'s requirement that every decision carries a rationale (it becomes optional).

## Context

The resolver chooses a product among search results and a quantity. [ADR-0005](0005-model-decides-executor-acts.md) describes its output as a candidate, a quantity, a confidence and a rationale.

A new class of models, "System One" decision models, is built specifically for this. They take unstructured state and return a typed choice with calibrated probabilities, in milliseconds:
- [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (API, early access, no access yet);
- [Julia-1](https://huggingface.co/SupersonicLabs/Julia-1) (open source, Apache 2.0, 144M params, runs on CPU, released 2026-09-26).

Their constraints shape the design:
- 2–20 options per question;
- no generated text, so no rationale;
- no arithmetic.

The first HLD draft asked the product and quantity questions in two separate calls. Johann caught this in review: one call gives the model all the product information in the same context and halves the latency.

## Options considered

1. **Free-form LLM call** returning a structured decision. Works with any chat model. Confidence is self-reported, not calibrated.
2. **Two typed questions in two calls:** product first, then quantity options for the chosen product. Quantity options can be product-specific ("2 trays"). But the context is split and latency doubles.
3. **One typed question over (product × quantity) pairs.** A single call, but it goes over 20 options almost immediately.
4. **Two typed questions in one call, with a product-independent quantity question.** Product is chosen among ≤ 20 candidates. Quantity is chosen among *target amounts* (`1 unit`, `~500 g`, `~1 kg`), and the executor converts the target into the chosen product's unit of sale.

## Decision

Option 4.

- **The call:** one per item. The state holds the requested item, its constraints, its preferences and the full candidate details. The two questions are the product and the target quantity.
- **Keeping it under 20 options:** the catalog adapter pre-ranks candidates to at most 20.
- **No quantity on the list:** the quantity question isn't asked. The target is `default_quantity` from the preferences, or 1 unit flagged `QUANTITY_ASSUMED` ([ADR-0008](0008-low-confidence-decisions-added-and-flagged.md)).
- **Quantity conversion:** the executor converts the target amount into the product's unit of sale deterministically, and marks the result when the conversion is inexact.
- **Backends:** the resolver sits behind a `DecisionBackend` interface. Implementations are **Claude Haiku 4.5** (baseline, an LLM with structured output), **Julia-1** (local) and **Jev** (placeholder until there's access). The evals compare them on product match, quantity accuracy, calibration, latency and cost, and the default is chosen from those numbers.
- **Rationale is optional.** When the backend doesn't provide one, the report shows the top alternatives with their probabilities instead.

## Consequences

- The resolver's questions don't depend on which backend answers them. Switching backends is configuration.
- Unit arithmetic lives in one place, the executor, and is unit-tested. It is never inferred by a model.
- Explanations get weaker with calibrated backends: alternatives and probabilities instead of a sentence. For a reviewer scanning a cart, that is often more useful, but it is a real change from ADR-0005.
- Julia-1 was three days old when this was written. It's treated as one more backend under evaluation, not as a bet.
