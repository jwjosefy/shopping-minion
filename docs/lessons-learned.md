# Lessons learned from alfa0

_Drafted by Claude on 2026-10-01, for Johann's review. Written at the reset described in [project-reset.md](project-reset.md). The first implementation lives in [`alfa0/`](../alfa0/)._

## Where alfa0 ended

Two days, 80 commits, 13 ADRs, an HLD and an LLD, about 4,200 lines of source and 3,100 of tests (321 passing). **Not one item was added to a real cart.**

What worked end to end: photo → intake → review screen → resolver on recorded candidates → report with a dry-run cart. What never worked: searching the store from the app. Two supervised runs of the discovery agent (40 and 80 steps, about US$0.06 together) didn't produce a site profile. The second never called `try_search` and stopped at step 51, after spending its steps looking for product page URLs.

## What alfa0 gives the reset

### Design decisions that stay

- **A human reviews the transcribed list before anything is searched** (alfa0 ADR-0002). A real list mixes slashes that separate items with slashes that add a constraint ("feijão normal / preto não"). The new flow keeps the review as step 3.
- **The model decides, deterministic code acts** (alfa0 ADR-0005). The new model makes this even plainer: `search` and `add_cart` are pure Python, and `decide` is the only step that calls a model.
- **The store is used through its site, in a browser, as a user would** (alfa0 ADR-0012). No HTTP client, no hand-built `fetch()`, no copied hosts or ids. alfa0 learned this the hard way: calling the search endpoint directly brought a Cloudflare 403, CORS errors, a cross-domain flag, and a placeholder catalog returned without an error. All of it had to be removed.
- **Quantity comes from a rule, not from the model** (alfa0 ADR-0013): the list's quantity, then the preferences, then 1 unit flagged for review.
- **Secrets with dotenvx**, with the private key in the OS keyring and a backup in Bitwarden (journal 0003). Watch for this: `dotenvx run` exits 0 when decryption fails and passes the `encrypted:` strings through as values.
- **Confidence thresholds have to be calibrated per backend.** One fixed 0.8/0.5 can't serve Haiku and Julia-1 together (journal 0006).
- **Purchase history is the fastest source of preferences** (journal 0008). It is the reason for the new SQLite store of history, inputs and corrections.

### Facts about Andorinha (observed, not assumed)

- Search is a page: `andorinhaonline.com.br/busca/<term>`. The page fetches results by itself and shows "Encontramos N itens".
- **A user agent containing `HeadlessChrome` gets 0 results.** A regular desktop Chrome user agent works with or without a window. That rests on six browser configurations tested on one day (alfa0 ADR-0012).
- The page's own search response carries `hits[]` with `pricing`. The page asked for results in pages of 12 (`from=0`, `from=12`). Seen in the S1 logs.
- Weight-sold products use a stepper with a product-specific increment: 0.1 kg for sliced chicken breast, 0.3 kg for sliced ham, 0.9 kg for a whole piece. Packs appear only in the name ("C/16 Rolos", "Leve 16 Pague 15"). Seen in journal 0005, through code that was later removed, so it should be checked again in the page.
- Product cards navigate with JavaScript. A stable product URL was never confirmed.
- The site shows a cookie banner that has to be dismissed.
- An anonymous cart works for testing. A logged-in account keeps the cart across browsers.

### Evaluation assets

- `list-001`: the first real list, 29 lines with the hard cases tagged. Gemini 3.5 Flash read 32 of 34 items.
- The resolver cases: 7 cases with candidate lists and labels that say which *kind* of product is right, not which brand. Haiku 4.5: 7/7. Julia-1: 2/7 after the policy, 5/7 raw picks.
- These move to `alfa0/` with everything else, but they are the first thing worth copying back.

## What didn't pay off

- **A generic system before a single working path.** Site profiles, a discovery agent that writes them, a step language (open/fill/press/click/wait), field mappings with conditions, guards against forbidden scripts, a model per role, a LangGraph graph for a fixed sequence. Each piece was reasonable on its own. Together they put four layers between "search for atum" and the store, for a project with one store.
	- Side-effect: LangGraph is overkill in this scenario - **as a principle, complexity must be earned before introduced in a project**.
- **An agent to learn what a person learns in ten minutes.** The discovery agent was meant to save writing a scraper by hand. Writing that scraper by hand is what the reset does now. Again - overkill for the context.
- **The per-item loop.** Search, decide and add for each item, one at a time, meant the slowest step paced everything and the model only ever saw one item. The prototype Johann built with his son ran each operation over the whole list. That is faster end to end, and it lets the decide step batch its calls.
- **Each problem answered with more structure.** When a draft profile had the wrong fields, the answer was a new tool, a new rule in the prompt, a new option in the reader. Nobody stopped to ask what the simplest thing that works would be. That question was mine to ask, and I didn't ask it.

## About working together

### What worked

- **Being interviewed before anything is written.** The questions caught a contradiction and removed an LLM orchestrator that would have decided nothing (journal 0004).
- **Reviewing inside the document** (the Obsidian loop). Answers written inline under each question are unambiguous. This reset document works the same way.
- **ADRs start as Draft.** An agent can write one; only Johann accepts it.
- **"Don't write what you didn't observe."** Inferences are labeled, untested causes aren't stated, and the number of cases behind a claim is given. This caught a made-up "platform vendor" label and a cause resting on two tests.
- **Task agents in worktrees, reviewed wave by wave.** Mechanically it worked: tests passed in every wave, and the review caught an integration bug (product URLs without the base URL) and a wrong ordering in the LLD.

### What didn't

- **An unattended run on a detailed plan.** The plan had the flaw in it (an "open" option to call the store's JSON API), and the run built on it with nobody to stop it (journal 0007). A checkpoint at the first choice that changes how the system reaches the outside world would have caught it.
- **Options left "open" were read as permission.** Documents now say what is never done, not only what is preferred.
- **Caution nobody asked for.** Rejecting a regular user agent as "evasion" on Johann's own browser and account cost a round of review.
- **Process outgrew the product.** HLD, LLD, waves, briefs, ADRs and journals were all useful as a record, but they were planning a system the size of the abstraction, **not the size of the problem**. The review cycles were fast. What they reviewed was too big.

## For the reset

1. **Make it work, then generalize.** One store, one hand-written scraper, a real cart. Abstractions only when a second case shows up.
2. **Three passes over the list:** search everything, decide everything, then add everything. Show progress in the web app while the browser works.
3. **Put a real cart in the first milestone,** even with three items, before the review screens get polished.
4. **Keep the rules that held:** the browser as a user, the model decides and code acts, checkout is manual, ADRs as drafts, no claim without an observation.
5. **Checkpoint at route changes, not at task boundaries.**
6. **Keep the system-design simple enough to support the end-goal**. Over-engineering was clearly a fault in the alfa0 round.
