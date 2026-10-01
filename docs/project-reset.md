# Project reset

_Oct. 1st. Written by Johann in Portuguese; translated to English by Claude on 2026-10-01. The original is [project-reset.pt-BR.md](project-reset.pt-BR.md)._

# Intro

Building the project stopped yesterday afternoon because I hit the limit on my Claude plan. I could have kept going with another model, but I decided to stop, focus on other things and take a step back.

Looking in hindsight, the project got needlessly complicated and messy. N layers, abstractions on top of abstractions, and at the end of it we STILL don't have a working model.

I want you to write a [[lessons-learned]] inside docs/ with what we actually kept, both from the design and from my interaction with you.

### Self-reflection

The mental model I was following for the core of the shopping was a master loop over the list items, running the steps for each item, something like:

``` ORIGINAL MODEL
loop (each item) {
	search()     -- use the original item name to search
	decide()     -- choose which option to pick
	add_cart()   -- execute the action to put in the cart
}
review()
```

Looking back at the prototype I built with my son, the approach there was different: it repeated the loop for each operation instead of trying to have a single loop. In practice, that makes the whole operation much faster end to end, which is the final goal of saving the user's time (see [[#Reset definition]]).

``` NEW MODEL
loop (each item) { search() } -- 100% deterministic python code, simple scraper
loop (each item) { decide() } -- optimized calls to Jev/models, allow better context
loop (each item) { add_cart()} -- 100% deterministic python

review()
```

# Reset definition

Goal: build an automation plus a web app to shop at andorinhaonline.com.br. It reads (OCR) a handwritten shopping list and ends with the cart built and ready for human review and checkout. The final goal is to cut the manual effort of building a cart for longer lists (50+ items), saving the user's time.

## Out of scope

- automatic checkout, in any form
- automating other stores in v0
- cloud hosting in v0
- mobile app
- docker

## Expected flow to automate

1. The user uploads the shopping list.
2. The app does OCR of the list with an LLM ([[#note-1]]).
3. The app asks the user to review/edit the list.
4. The app starts searching the site (search loop).
	1. For each item searched: capture the results (top ~15) from the page for matching later (name, brand, unit of measure, price, discount, possible units [un/kg]).
	2. A progress bar in the web app while Python drives the site.
5. The app does the matching (decide()).
	1. For each item: build a standardized query to Jev to choose the most likely product, given (original list item)<->(options found).
	2. Loop → eval for every item, 1 call per item or batches of 5.
	3. For high-confidence options: take that product.
	4. Medium-low confidence: show a choice screen to disambiguate the item.
		1. In a loop, item by item, one at a time, for the user to choose.
	5. Offer the user a last review and adjustment.
6. The app builds the cart (add_cart).
	1. For each item: navigate the site to the item and perform the action to add it to the cart.
	2. Careful with the interaction: it must click the plus (+) button several times. Handle timeouts and race conditions.
	3. A progress bar in the web app while Python drives the site.
7. The app tells the user it's done and offers to open the site with the cart open.

### note-1

Note that "do OCR of the list" can be done in several ways. To save money, design a way to invoke claude with -p, model haiku, passing the input file as a reference and directing the output to structured JSON.

## Output after the redesign: expected architecture

Move ALL of the current implementation into an alfa0/ subfolder, and start a new implementation in the project folder.

- A simple Python web app (FastAPI with vue.js and a modern, interesting dark theme).
- Playwright driving Chrome:
	- headless=false mode, showing the site as it navigates;
	- a proper user agent, like a regular browser.
- Local SQLite in data/ to keep history, inputs and corrections.
	- This information will later evolve into a preferences mechanism, to steer the matching better.
- New HLD.
- New LLD, only after review and a stamp on the HLD.
	- Implementation plan (PLAN = Opus; RUN = Sonnet).

Keep using dotenvx for secrets.

# Questions before starting

1. "Jev": what is it? A specific model or service, or a typo (Julia-1? LLMs in general?).
	> A System One-style model from typesafe.ai, which is actually what inspired Julia-1. We'll start with Jev; I'll put the API key in .env, and later we'll test with Julia-1.

2. Opening the site with the cart at the end: the cart lives in Playwright's Chrome session. Either we leave that window open at the end, or a login is needed for the cart to show in your browser. This goes into the HLD as a decision. Do you have a preference?
	> Keeping Playwright open solves it for now. For now I'll log in to Andorinha by hand, so the cart stays on the account even if I switch browsers.

3. Old ADRs, journal and evals: do they all go to alfa0/, or do docs/adr and docs/journal stay at the root as history, with new ADRs superseding the old ones? I assume the eval fixtures (list-001, resolver-cases) get reused.
	> EVERYTHING goes to alfa0, except docs/journal. Also change CLAUDE.md so it ignores the folder and doesn't follow the ADRs there.
	> docs/journal records the journey and is the basis for the blog later on. It must be treated as append-only.

4. The unmerged fix branch (worktree agent-abdb617314a348016, the discovery agent fixes and S1 run 2): discard it, or merge it before the move so it's on record in alfa0?
	> Merge before the move, to keep the record.
