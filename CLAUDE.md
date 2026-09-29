# Shopping Minion — working agreement for Claude Code

## What this project is
An agent that turns a photo of a handwritten grocery list into a ready-to-review online cart. Public repo, built in the open: the reasoning is as much a deliverable as the code.

## Architecture (see README and docs/adr/)
Intake (OCR + parsing) → human review (web UI) → Preferences → Catalog (deterministic, via adapter) → Resolver (LLM, structured output, no write tools) → Executor (deterministic, validates, then acts) → Cart → human checkout.

Non-negotiables:
- The model never browses the store and never writes to the cart directly (ADR-0005).
- Store-specific code lives only under the catalog adapter (ADR-0004).
- Checkout is always manual.

## Secrets and personal data (ADR-0001)
- Secrets live in `.env`, encrypted with dotenvx. Run things with `dotenvx run -- <cmd>`.
- NEVER read, print, cat, or commit `.env.keys`. Never echo decrypted secret values.
- Never commit anything under `data/`, `inbox/`, `.auth/`, browser storage state, screenshots, or traces.
- Before every commit, check `git status` for files that look personal (order history, photos, cookies). If in doubt, stop and ask.

## Documentation conventions
- **ADR** (`docs/adr/NNNN-kebab-title.md`) for any decision that is costly to reverse or that a reviewer would ask "why?" about. Format: Status, Date, Context, Options considered, Decision, Consequences. Never rewrite an accepted ADR's decision; supersede it with a new one.
- **Journal** (`docs/journal/YYYY-MM-DD-kebab-title.md`) for what was tried, what was rejected, and changes of mind. Entries are append-only history: don't edit old entries to match new decisions; write a new entry.
- When a change contradicts an ADR, stop and propose a new ADR first.

## Code conventions
- Python 3.12+, managed with `uv`. Source in `src/shopping_minion/`, tests in `tests/`.
- Contracts between components are Pydantic models.
- `uv run ruff check . && uv run pytest` must pass before committing.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), small and focused. Commit history is part of the audit trail.

## Evaluation
- Fixtures in `evals/fixtures/` are anonymized ground truth. `list-001.yaml` is the first real list; its tags describe what makes each line hard.
- Any change to intake or resolver should be run against fixtures and the result noted in the PR/commit message.
