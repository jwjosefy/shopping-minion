# ADR-0002: A human reviews the transcribed list before anything else runs

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

The input is a photo of a real list, handwritten by different people, in cursive, with crossed-out words. The first real sample ([`evals/fixtures/list-001.yaml`](../../evals/fixtures/list-001.yaml)) shows that the hard part is not only reading the handwriting but interpreting the structure of each line:

- `/` usually separates items (`Açúcar / refri / água gás` is three items) but sometimes it doesn't: in `Feijão normal / preto não`, the slash introduces a constraint (*regular beans, not black beans*).
- Quantities sometimes appear inline (`Presunto 600 g`) and sometimes don't.
- Brand names are misspelled (`Filtro melita` → Melitta).
- Some lines are categories, not products (`Lanches das crianças`).
- The same item can appear twice (`Requeijão`, lines 19 and 28).
- Crossed-out words have to be ignored.

Every mistake here propagates silently: a misread item produces a confident, wrong product in the cart, and the reviewer at checkout has to catch it among 30 lines.

## Options considered

1. **Trust OCR output and review only at checkout.** Fewest steps, but errors surface late, far from their cause, and mixed with resolution errors.
2. **Confidence threshold: auto-accept high-confidence lines, ask about the rest.** Good long-term, but needs calibrated confidence that v0 doesn't have.
3. **Always review the transcribed list in a minimal web UI before resolution.** The user sees the photo next to the parsed items and can edit, split, merge, delete, or add lines.

## Decision

Option 3 for v0. The review step is the boundary between perception and everything downstream: nothing is searched until the list is confirmed.

The UI is deliberately minimal: photo on one side, an editable list of items (name, quantity, unit, notes/constraints) on the other, and one confirm button.

## Consequences

- Downstream components receive clean, human-confirmed input, so resolution quality can be evaluated independently from OCR quality.
- Every correction is a labeled example: the diff between the model's transcription and the confirmed list becomes evaluation data for the intake step at no extra cost.
- v0 needs a small web UI, which the original scope excluded (see the [journal](../journal/2026-09-29-0002-ocr-needs-a-human.md)).
- Option 2 becomes possible once enough corrections have been collected to measure where transcription actually fails.
