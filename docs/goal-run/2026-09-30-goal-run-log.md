# Goal run — 2026-09-30

Goal from Johann: advance every milestone (M2 to M6) unattended, document minor blockers here for
morning review, and stop and document at a major one. This file is the log and the summary.

## Summary

| Milestone | Status | What exists |
|---|---|---|
| M2 discovery, part 1 (search + units of sale) | **Done, by hand** | Working search profile for Andorinha (`profiles/andorinha/`), generic adapter, recorded fixtures, regression tests, live check of the 3 v0 items. The discovery **agent** did not complete a run: see blockers 1 and 2. |
| M3 resolver | **Done** | `DecisionBackend`, Haiku 4.5 backend, preferences, quantity conversion, confidence policy, resolver eval on 7 cases (7/7 with Haiku). |
| M4 discovery part 2 + real cart executor | **Blocked (major)** | Nothing built: needs the store credentials and a supervised run. The workflow already stops on a site change and reports it. |
| M5 workflow, report, web | **Done, with a dry run** | LangGraph per-item graph, run store, progress, report screen. Checked end to end with the real store search and Julia-1: upload → review → search → decision → report (11 s). The cart step is a dry run. |
| M6 Julia-1 backend + comparison | **Done** | Local backend, backend factory, eval harness, first comparison table (`docs/journal/2026-09-30-0006-...`). Thresholds still need calibrating per backend. |

136 tests pass and `ruff check` is clean. Everything is committed and pushed (`git log` for the list).

## Major blockers (why the run stopped)

### 1. OpenRouter has no credit (this is the one to fix first)
- The account is on the **free tier** (`is_free_tier: true`) with only US$ 0.11 used. Requests that
  reserve more than a few cents are refused with `402 in_flight_budget_exhausted`. Tiny calls still work.
- It stopped: the discovery agent (it failed on its first call), the GLM resolver comparison, and
  the intake fallback to GLM. Intake on Gemini and Julia-1 still work; the Haiku resolver does not.
- **To fix:** add credits at https://openrouter.ai/settings/credits. Then rerun, one LLM job at a
  time (two at once made the in-flight check fail earlier):
  `dotenvx run -- uv run shopping-minion discover andorinha-agent --headed --allow-domain osuper.com.br`

### 2. Store credentials are empty (blocks M4)
- `STORE_EMAIL` and `STORE_PASSWORD` aren't set (checked by presence only, values never read).
  Login and cart discovery need them, and they act on your real account, so ADR-0006's supervised
  run applies. I didn't try to work around it (for example with an anonymous cart).
- **To fix:** `dotenvx set STORE_EMAIL ...` and `dotenvx set STORE_PASSWORD ...` in your terminal.

### Also empty
- `ANTHROPIC_API_KEY`: the planned Opus 5.5 discovery model and the direct Haiku backend. Everything
  runs through OpenRouter or Gemini meanwhile.

## Decisions that need your review

1. **ADR-0012 (Proposed):** profiles may need a visible browser and requests made from a page, and a
   human can allow an extra API host for discovery. Andorinha's search only works this way (see the
   journal entry 0005). Nothing about the browser's identity is changed. Please also check the
   store's terms of use before real use; the code can't decide that.
2. **ADR-0011 (Accepted, by your answer):** intake falls back from Gemini to GLM. Currently the GLM
   half doesn't work (blocker 1).
3. **Quantity question not asked (deviates from HLD §4.5):** the target quantity is derived from the
   list, then the preferences, then 1 unit flagged `QUANTITY_ASSUMED`. Nothing was left to ask for
   the v0 items. No ADR written. Tell me if you want the question back.
4. **The Andorinha profile is hand-written**, from a network probe, and says so in its `notes`. The
   agent's own run is the check that discovery works; it's pending on blocker 1.

## Minor issues and things to know

- **Confidence thresholds (0.8 / 0.5) are placeholders.** Haiku fits them; Julia-1 doesn't (its
  probabilities spread across equally good products). Calibrate per backend with more cases.
- **No preferences file for the v0 items.** `data/preferences.yaml` is the example. A weight-sold
  item with no quantity ("filé de peito de frango") becomes one 100 g step, flagged approximate. Add
  a `default_quantity` in kg for it.
- **The real end-to-end run needs a display.** The search only works in a visible Chromium, so it
  opens a window while it runs.
- **Pack detection is a regex on the name.** It reads "C/16 Rolos" and "Lv12 Pg11" but not "Leve 16
  Pague 15" or "Pacote 24un" (those show as single units).
- **No stable product URL**: candidate links point to the search for the product's name.
- **The store id** (`269/1327`) is what the site selects by default. If your pickup store differs,
  the profile's search URL needs the right ids.
- **Julia-1 isn't a project dependency.** It's installed by hand (README, "Julia-1 backend"),
  because it needs torch. The install pulled a CUDA build of torch (5.7 GB venv); run things with
  `uv run --no-sync` afterwards, or a plain `uv sync` removes it. Weights are in `data/models/`
  (git-ignored).
- **OpenRouter's `max_tokens`:** without a cap each call reserves 131k output tokens. The discovery
  role has 8000, the resolver 2000.
- **The Gemini free tier** is 20 requests/day/model. `gemini-3.8-flash` returned 503 twice.
- **My mistakes, fixed:** a `git checkout` on a test file discarded uncommitted tests (rewritten);
  `pkill -f` killed its own shell once; a stray empty directory shadowed the `julia` package until I
  removed it. The eval harness also had a wrong label of mine (word order in a regex); the model was
  right.
- **A found bug:** saved reports couldn't be read back (computed fields vs `extra="forbid"`). Fixed,
  with a test.

## What to do in the morning

1. Add OpenRouter credits (or set `ANTHROPIC_API_KEY`) and rerun the discovery agent (command above).
   If it produces a profile equivalent to the hand-written one, discovery is validated.
2. Read ADR-0012 and the two journal drafts (0005, 0006); edit them into your voice or delete them.
3. Fill `STORE_EMAIL` / `STORE_PASSWORD` and start M4 together: login and cart discovery, then the
   cart executor (`CartExecutor`, currently a dry run in `services.py`).
4. Add your real preferences to `data/preferences.yaml`, then rerun `resolver_eval.py`.
5. Pin the repo, if you haven't yet.
