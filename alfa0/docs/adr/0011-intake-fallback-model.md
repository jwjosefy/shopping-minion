# ADR-0011: The intake role falls back to a second model when the first one fails

- **Status:** Accepted
- **Date:** 2026-09-30
- **Supersedes:** the "no fallback in v0" clause of [ADR-0009](0009-langgraph-workflow-model-per-role.md). The rest of ADR-0009 stands.

## Context

[ADR-0009](0009-langgraph-workflow-model-per-role.md) left fallbacks out of v0, with the config field reserved. Running the system for real showed where that hurts:

- **Intake runs on a free Gemini key**, limited to 20 requests per day per model. `gemini-3.8-flash` also returned `503` ("high demand") twice.
- **A second model is already set up:** GLM 5.3 Flash via OpenRouter (commit `e14d46c`). On `list-001` it scored 33/34 items against Gemini's 32/34. The two make different mistakes, so neither is clearly better.

Intake is where a failure is most visible, because it's the first step after the upload. The user is waiting on the screen.

## Options considered

1. **Keep no fallback.** When the free quota runs out, the upload fails until the next day.
2. **Switch intake to GLM.** No quota problem, but Gemini read the handwriting more accurately on the only fixture we have.
3. **Gemini first, GLM on any failure**, one level only.
4. **LangChain `with_fallbacks`.** Less code, but it hides which model answered, and it doesn't compose with the structured-output wrapper both models need.

## Decision

Option 3, implemented in the intake itself rather than through `with_fallbacks`.

- A chat role can have one `fallback` role in `config/models.yaml`. A fallback can't have its own fallback.
- The intake tries the primary. On **any** exception (quota, overload, network, a malformed response), it logs a warning naming the model and the error, then tries the fallback once. If the fallback also fails, the error is shown to the user, as before.
- The intake records which model answered (`answered_by`), so runs and evals can tell the two apart.
- Only intake has a fallback for now. Discovery runs supervised and rarely, so a failure there is better seen than silently worked around.

## Consequences

- Uploads keep working when the free Gemini quota runs out.
- A transcription can come from either model. Evals must say which one they measured, and they do, through `--model` overrides and the output file name.
- A failure that affects both providers still reaches the user.
- The fallback model still sees the family's handwriting, so the privacy note about free tiers applies to both providers.
