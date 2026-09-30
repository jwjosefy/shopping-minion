# Shopping Minion — working agreement for Claude Code

## What this project is
An agent that turns a photo of a handwritten grocery list into a ready-to-review online cart. Public repo, built in the open: the reasoning is as much a deliverable as the code.

Read before working: `docs/hld.md` (design), `docs/lld.md` (the tasks for the rest of v0, and the brief template for task agents), `docs/adr/` (decisions), `docs/goal-run/` (state and blockers after unattended work).

## Architecture
Photo → Intake (vision model) → human review (web app) → Workflow (LangGraph, fixed steps) → per item: Catalog search → Resolver (decision model picks a product) → Executor (deterministic: converts quantity, validates, adds to cart, verifies) → Report → human checkout.

Non-negotiables:
- **The store is used only through its site, in a browser, the way a user would.** Never call the site's endpoints directly: no HTTP client, no `fetch()` with hand-built requests, no copied hosts, ids or request parameters (ADR-0012). If something seems to need it, stop and ask.
- At shopping time no model drives the browser, and the model never writes to the cart (ADR-0005). The discovery agent is the only model that browses, before shopping and supervised (ADR-0006).
- Store-specific knowledge lives in a site profile, never in the core (ADR-0004, ADR-0006).
- Checkout is always manual. No tool or code path may place an order.

## What exists and what doesn't (2026-09-30)
- **Built and tested:** contracts, model config per role, browser provider, intake with fallback, web app (upload, review, run, report), run store, resolver (LLM and Julia-1 backends), quantity conversion, confidence policy, LangGraph workflow, intake and resolver evals.
- **Not built:** the catalog adapter, the discovery agent, site profiles, login and the cart executor. `default_services` refuses to start a run; the cart is a dry run.

## How to work here
- **ADRs are always created with `Status: Draft`** and wait for Johann's review. Never write `Accepted` or `Proposed` yourself, and never change an accepted ADR's decision. When a change contradicts an ADR, stop and ask before writing code.
- **Don't write what you didn't observe.** Label inferences as inferences. Don't state a number you didn't measure or a cause you didn't test. If only two cases were tested, say which two.
- **A design option left "open" in a document is not permission.** If the work starts to depend on a choice that changes how the system reaches the store, the models or the data, stop and ask.
- **Stay inside the task.** Don't add flags, workarounds or exceptions to get past a guardrail; report the blocker instead.
- **Journal entries are Johann's voice.** If you draft one, mark it as a draft written by Claude at the top.
- Commit and push only what was asked. Small Conventional Commits; the history is part of the audit trail.

## Secrets and personal data (ADR-0001)
- Secrets live in `.env`, encrypted with dotenvx. Run things with `dotenvx run -- <cmd>`. The private key is in the OS keyring, backed up by Johann.
- NEVER read, print or commit `.env.keys`. Never echo decrypted values. To check a key, test only whether it is set.
- Never commit anything under `data/`, `inbox/`, `.auth/`, browser storage state, screenshots or traces.
- Before every commit, check `git status` for files that look personal (order history, photos, cookies), and run `gitleaks protect --staged`. If in doubt, stop and ask.
- Model calls cost money. Run one paid job at a time, and say what a run will call before starting it.

## Documentation conventions
- **ADR** (`docs/adr/NNNN-kebab-title.md`): Status, Date, Context, Options considered, Decision, Consequences. Supersede, don't rewrite.
- **Journal** (`docs/journal/YYYY-MM-DD-NNNN-kebab-title.md`, `NNNN` a global sequence from 0001 in order of creation): what was tried, what was rejected, changes of mind. Append-only: correct an old entry with a dated note at its top or with a new entry.
- **Goal-run log** (`docs/goal-run/`): state, blockers and decisions waiting for review after unattended work.

## Code conventions
- Python 3.12+, managed with `uv`. Source in `src/shopping_minion/`, tests in `tests/`.
- Contracts between components are Pydantic models (`contracts.py`).
- `uv run ruff check . && uv run pytest` must pass before committing. Tests that hit real sites are marked `live` and don't run by default.
- One model per role in `config/models.yaml` (ADR-0009). API keys are named by env var, never written in config.
- Julia-1 (optional resolver backend) is installed by hand, see README; use `uv run --no-sync` with it.

## Evaluation
- `evals/fixtures/list-001.yaml`: ground truth for intake (the first real list). `evals/fixtures/resolver-cases.yaml` with `evals/fixtures/candidates/`: ground truth for the resolver.
- Any change to intake or resolver is run against the fixtures, and the result goes in the commit message. Fix a wrong label, never the result.
