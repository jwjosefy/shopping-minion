# 2026-09-29 — OCR needs a human (scope change)

This morning's scoping entry listed "a UI" as out of scope for v0. That lasted about an hour.

I looked at a real list: 29 lines written in cursive by someone else, with crossed-out words and a lot of implicit structure. Reading the handwriting is only part of the problem. The same character means different things on different lines: in `Açúcar / refri / água gás` the slash separates three items, while in `Feijão normal / preto não` it introduces a constraint (*regular beans, not black beans*). A model that gets that wrong doesn't fail loudly. It puts black beans in the cart with full confidence.

So v0 gets a review step between the photo and everything else: a minimal page showing the photo next to the parsed items, which I can edit before confirming. Rationale in [ADR-0002](../adr/0002-human-review-of-transcribed-list.md).

Two things I like about this, beyond catching errors:

1. **It isolates failures.** If the cart is wrong, I know the input was right, so the fault is in resolution, not in reading.
2. **Every correction is free evaluation data.** The diff between what the model read and what I confirmed is exactly the dataset I need to measure transcription quality later.

The list itself is now the first evaluation fixture ([`evals/fixtures/list-001.yaml`](../../evals/fixtures/list-001.yaml)), with each line tagged by what makes it hard: multi-item lines, negations, misspelled brands, inline weights, duplicates.

Decisions so far:

| # | Decision |
|---|---|
| [0001](../adr/0001-secrets-with-dotenvx.md) | Secrets with dotenvx, committed encrypted |
| [0002](../adr/0002-human-review-of-transcribed-list.md) | Human reviews the transcribed list before resolution |
| [0003](../adr/0003-preferences-as-static-yaml.md) | Preferences start as a hand-written YAML |
| [0004](../adr/0004-store-catalog-adapters.md) | Store access through a catalog adapter |
| [0005](../adr/0005-model-decides-executor-acts.md) | The model decides; a deterministic executor acts |
