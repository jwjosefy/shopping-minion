# Shopping Minion — working agreement for Claude Code

## What this project is
Automation plus a web app that turns a photo of a handwritten grocery list into a cart at andorinhaonline.com.br, ready for a human to review and check out. The goal is to cut the time it takes to build a cart for long lists (50+ items). Public repo, built in the open.

Read before working: `docs/project-reset.md` (the reset and its scope), `docs/lessons-learned.md`, and the new `docs/hld.md` / `docs/lld.md` once they exist.

## alfa0/ is an archive
`alfa0/` holds the first implementation, with its HLD, LLD, ADRs, evals and goal-run logs. **Ignore it.** Its ADRs don't apply here, and its code isn't a base to build on. Read from it only when Johann asks, or to copy a specific asset he approves (e.g. an eval fixture). Never edit it.

## Shape of the system
Three passes over the list, not one loop per item:
1. **search**: deterministic Python driving the store's site with Playwright, capturing the top ~15 results per item.
2. **decide**: model calls (Jev first, Julia-1 later) pick the product for each item. High confidence is accepted; medium or low goes to the user, one item at a time.
3. **add_cart**: deterministic Python adds each item, clicking the stepper as needed.

## Jev (the decide step)
Jev is TypeSafe's "System One" decision model (docs: https://docs.typesafe.ai/introduction, index at https://docs.typesafe.ai/llms.txt). It is the model that inspired Julia-1. It does not generate text: it takes a `state` plus typed questions and returns typed answers with calibrated probabilities.
- Question types: **Choice** (pick one of up to 255 options; returns `choice`, `probabilities`, `confidence`), **Score** (rate on ordered levels) and **Noul** (probability that a statement is true). Several questions go in one call and are evaluated independently.
- API: `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer $TYPESAFE_API_KEY`, model `jev-latest` (currently `jev-1.13`). Python SDK: `typesafe-sdk` (`TypeSafeClient().system_one(state=..., questions=...)`), which reads `TYPESAFE_API_KEY` from the environment.
- Billed per input token (output is free). Context: 64k tokens per request, 32k for the state plus the longest question.
- Known weak spots (jev-1.13): reads instructions literally, can't do math or counting, gets worse with irrelevant state. So: keep arithmetic and unit conversion in code, send only the fields the question needs, and write exact criteria for each option.
- Choice and Noul answer different questions. A Choice says *which* option; a Noul per option says whether *any* fits. Thresholds don't carry over between them.

Non-negotiables:
- **The store is used only through its site, in a browser, the way a user would.** No HTTP client, no `fetch()` with hand-built requests, no copied hosts, ids or request parameters. If something seems to need it, stop and ask.
- No model drives the browser, and no model writes to the cart.
- Checkout is always manual. No code path may place an order.
- Keep it simple: one store, no generic layers until a second case exists.

## How Johann reviews (follow this pattern)
When you need Johann's input (interview, design questions, open points), don't ask in the chat. Write a `.md` in `docs/` (e.g. `docs/hld-interview-NNN.md`, or an "Open questions" section in the doc under review) with numbered questions, each with your proposal. Johann answers inline with `>` in Obsidian, then tells you in the chat. Read the answers, fold them into the documents, and report back. Keep questions direct, and don't ask what `docs/project-reset.md` already answers.

## How to work here
- **Don't write what you didn't observe.** Label inferences as inferences. Don't state a number you didn't measure or a cause you didn't test.
- **ADRs are always created with `Status: Draft`** and wait for Johann's review. Never mark one accepted yourself.
- **A design option left open is not permission.** If the work depends on a choice that changes how the system reaches the store, the models or the data, stop and ask.
- **Stay inside the task.** Report blockers; don't add flags or workarounds to get past them.
- **The HLD needs Johann's approval before the LLD is written.** Plans are written by Opus, tasks run by Sonnet.
- Commit and push only what was asked. Small Conventional Commits.

## Journal
`docs/journal/` records the whole journey, alfa0 included, and feeds the blog. **It is append-only:** never rewrite an entry. Correct one with a dated note at its top or with a new entry. Name entries `YYYY-MM-DD-NNNN-kebab-title.md`, with `NNNN` continuing the global sequence. Entries are Johann's voice: if you draft one, mark it at the top as drafted by Claude.

## Secrets and personal data
- Secrets live in `.env`, encrypted with dotenvx. Run things with `dotenvx run -- <cmd>`. The private key is in the OS keyring, backed up by Johann.
- NEVER read, print or commit `.env.keys`. Never echo decrypted values. To check a key, test only whether it is set.
- Never commit anything under `data/`, `inbox/`, `.auth/`, browser storage state, screenshots or traces.
- Before every commit, check `git status` for personal files and run `gitleaks protect --staged`.
- Model calls cost money. Run one paid job at a time, and say what a run will call before starting it.
