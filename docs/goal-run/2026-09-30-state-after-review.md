# State after review — 2026-09-30

This replaces the summary in [the goal-run log](2026-09-30-goal-run-log.md). That log is kept as written; parts of it describe work that was removed after Johann's review.

## What the review changed

- **Removed:** the catalog adapter, the discovery agent, the Andorinha site profile and its recorded responses, the `discover` and `search` commands, and the ADR-0012 the run had proposed. They called the store's endpoints directly, which is not how this project reaches a store.
- **Added:** [ADR-0012](../adr/0012-the-store-is-used-through-its-site-in-a-browser.md) as a **Draft** (the store is used through its site, in a browser, as a user would), new working rules in `CLAUDE.md`, and [journal entry 0007](../journal/2026-09-30-0007-goal-run-and-review.md).
- **Converted:** the recorded store responses became normalized candidate lists in `evals/fixtures/candidates/`, so the resolver eval no longer depends on a profile. The eval gives the same numbers as before.

## State by milestone

| Milestone | State |
|---|---|
| M0 skeleton, contracts, browser provider | Done |
| M1 upload, intake, review | Done. Intake on Gemini 3.5 Flash: 32/34 items on `list-001`. |
| M2 discovery, part 1 (search, units of sale) | **Not done.** To be built again under ADR-0012. |
| M3 resolver | Done. Haiku 4.5: 7/7 on the recorded cases. |
| M4 discovery, part 2 (login, cart) and cart executor | Not started. |
| M5 workflow, report, run screens | Done, with a dry-run cart. The app can't run a real list until M2 exists: `default_services` refuses to start. |
| M6 Julia-1 backend and comparison | Done. Julia-1: 2/7 after the policy, 5/7 raw picks. Thresholds need calibrating per backend. |

99 tests pass; `ruff check` is clean.

## Waiting for Johann

1. **Accept or change ADR-0012 (Draft).** Its two open questions were answered by Johann on 2026-09-30 and are now part of the text, with the browser test that followed.
2. **Decide whether the quantity question comes back.** The resolver derives the target quantity from the list, then the preferences, then 1 unit flagged as assumed (a deviation from HLD §4.5).
3. **Read journal drafts 0005, 0006 and 0007.** They are written by Claude in your voice.
4. **Whole-project review**, before any work is split into smaller tasks.

## Blockers for later work

- **OpenRouter has no credit** (free tier): the GLM intake fallback and the Haiku resolver fail with `402` on anything but tiny calls.
- **`STORE_EMAIL`, `STORE_PASSWORD` and `ANTHROPIC_API_KEY` are empty.**
- **Andorinha's search page shows no results when the browser sends `HeadlessChrome` in its user agent** (Playwright's default). With a regular desktop user agent it works, headless included. The six configurations tested are in ADR-0012. The browser provider doesn't set a user agent yet.

## Things to know

- Julia-1 is installed by hand (README), outside the lockfile. Its weights are in `data/models/` (git-ignored).
- The confidence thresholds (0.8 / 0.5) are placeholders.
- `data/preferences.yaml` is still the example file.
- The candidate fixtures were recorded through the removed code. They are public catalog data (product names, brands, prices), and their `url` field points to a search for the product's name.
