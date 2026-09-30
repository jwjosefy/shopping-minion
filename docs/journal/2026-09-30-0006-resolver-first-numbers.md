# 2026-09-30 — First numbers for the resolver

_Draft written during an unattended run on 2026-09-30. Johann to review before publishing._

The resolver ([ADR-0010](../adr/0010-single-pass-typed-resolver.md)) chooses one product among the store's search results. Two backends now run on the same recorded cases, so the comparison ADR-0010 promised has a first data point.

## The cases

Seven, from recorded Andorinha responses (`profiles/andorinha/fixtures/`), with ground truth in `evals/fixtures/resolver-cases.yaml`:

- the three v0 items: atum (sold by unit), papel higiênico (pack), filé de peito de frango (weight);
- three of the hard lines from `list-001`: "feijão normal / preto não" (a negation), "presunto 600 g" (an inline quantity), "leite" (the 17-kinds-of-milk problem);
- one case with no right answer: "atum", against a page of milk products.

Labels say which **kind** of product is right, not which brand: brands are the user's preferences (ADR-0003), not ground truth.

## Results

| | Correct after the policy | Raw picks correct | Wrong item would enter the cart | Latency | Cost |
|---|---|---|---|---|---|
| Claude Haiku 4.5 (OpenRouter) | **7/7** | n/a (first run predates the metric) | 0 | 2.8 s/item | not measured |
| Julia-1, with a "none of these" option | 2/7 | 3/7 | 0 | 0.13 s/item (5.7 s to load) | none, local CPU |
| Julia-1, without it | 2/7 | 5/7 | 1 (the no-match case) | 0.13 s/item | none |

"After the policy" means what the system would do: confidence ≥ 0.8 adds, 0.5–0.8 adds and flags, below 0.5 skips ([ADR-0008](../adr/0008-low-confidence-decisions-added-and-flagged.md)). These thresholds are placeholders.

## What the numbers say

- **Haiku is right on all of them**, including the no-match case (it answers "none" and the item is skipped). On this size of test, that's a baseline, not a proof.
- **Julia-1's raw picks are mostly right, but its probabilities are low.** Without the "none" option it picked the right kind of product for papel, frango, presunto, leite and atum, with confidence between 0.23 and 0.86. When several products are equally good, a calibrated model spreads its probability across them, so one exact product rarely gets 0.8. The fixed 0.5 threshold then throws away good picks as `NOT_SURE`.
- **The "none" option makes Julia-1 too cautious**: it answered "none of these" for leite (0.87) and filé de frango (0.76). Without it, the no-match case picks a milk product with 0.68.
- **So the thresholds can't be shared between backends.** They need calibrating per backend on more data ([ADR-0008](../adr/0008-low-confidence-decisions-added-and-flagged.md) said so; now it has a reason).
- **Preferences will change this.** Julia-1's confidence is the probability of one specific product. A known brand and size (ADR-0003) would make one candidate stand out.

## Things that went wrong on the way

- **A wrong label in my own ground truth.** The eval said Haiku failed the chicken case because it picked "Frango Filé Peito Sadia Pacote 1KG": my regex only knew the order "filé … peito … frango". The model was right. I fixed the label, not the result.
- **Julia-1's strict encoding refuses options that don't fit its 512-token head.** With 19 candidates the question and options overflow, so the backend drops the least relevant candidates until they fit (candidates arrive ranked) and uses shorter descriptions.
- **The pipeline found a real bug:** computed fields (`confidence`, `counts`) were written to the saved report and then rejected on load by the contracts' `extra="forbid"`. Now they're dropped on read.

## What isn't known yet

- Haiku on a bigger set of cases and with preferences: seven cases can't separate 7/7 from 6/7.
- Jev: no access.
- Whether a weight-sold item with no quantity written should default to one stepper step. With "filé de peito de frango" and no quantity, "1 unit" becomes one 100 g step, flagged as approximate. A `default_quantity` in kilos in the preferences is the fix, and the user has to provide it.
