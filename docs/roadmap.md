# Shopping Minion — Roadmap

- **Status:** Draft, for Johann's review
- **Date:** 2026-10-01
- **Role:** the source of truth for what comes next. The README's roadmap and the HLD point here. When a milestone's scope or order changes, it changes here first.

## How a milestone moves

1. **Defined here:** a goal, what's in and out, and a *done when* that can be checked.
2. **Designed:** its own LLD (`docs/lld-mN.md`), interviewed and reviewed through a `.md` with questions answered in Obsidian. If the milestone changes the design (a new data source, a new runtime), the HLD is updated first and re-approved.
3. **Built:** in waves of tasks, each reviewed before merge.
4. **Accepted:** Johann runs it for real. The acceptance goes in the LLD, and this file marks the milestone done with the date and a link to the evidence.

A milestone isn't done because its code is merged. It's done when its *done when* was observed.

## Overview

| # | Milestone | Status | Design |
|---|---|---|---|
| M0 | Look at the site | ✅ 2026-10-01 | [lld.md](lld.md) §1–§7 |
| M1 | CLI: three items into the real cart | ✅ 2026-10-01 | [lld.md](lld.md) §9 |
| M2 | Web app | ✅ 2026-10-01 | [lld-m2.md](lld-m2.md) §11 |
| M3 | Full list, photo to cart, measured | 🟡 closing | §M3 below |
| M4 | Preferences learned from history | ⬜ next | to design |
| M5 | UX improvements | ⬜ | Johann to elaborate |
| M6 | Julia-1 as a local decide backend | ⬜ | to design |
| M7 | Cloud migration | ⬜ | design TBD |

## M0 — Look at the site ✅

**Done when** a headed session observed search, product page, stepper and cart, and wrote it down. Done on 2026-10-01: [site-notes/andorinha.md](site-notes/andorinha.md).

## M1 — CLI: three items into the real cart ✅

**Done when** `shopping-minion run` put atum, papel higiênico and 1 kg of filé de frango into Johann's logged-in cart in the right quantities, checked against the reloaded cart. Done on 2026-10-01 ([lld.md](lld.md) §9).

## M2 — Web app ✅

**Done when** the whole flow ran in the browser, from the desktop and from the phone over the LAN, on a real list. Done on 2026-10-01 ([lld-m2.md](lld-m2.md) §11).

## M3 — Full list, photo to cart, measured 🟡

**Goal:** prove the point of the project on a real long list: less time than building the cart by hand.

**Already observed** (2026-10-01, list-001, 34 items):
- photo → 31 products in the cart, 31 of 31 checked;
- OCR 24 s;
- 32 manual picks.

**Missing to close it:**
- **Time, per step.** The run history only records when a run started (`runs.created_at`). So we can't yet say how long search, picking and filling took. Proposal: one timestamp per state change, kept with the run.
- **Corrections, counted.** Lines you edited, added or deleted in the list review (OCR vs confirmed list, both already saved); picks you made instead of Jev; quantities you changed in the cart review.
- **A baseline:** Johann's estimate, not measured. By hand, building an online cart of 30–50+ items takes over 1 h, and larger lists 2–3 h. Shopping in person takes 2 h or more, plus checkout and loading and unloading the car.

**Done when** one real list of 30+ items goes from photo to checked cart through the web app, and a short report states: time per step, total time against the baseline, and the three correction counts. The report is the starting line M4 has to beat.

## M4 — Preferences learned from history ⬜

**Goal:** Jev picks right more often, so you pick less. Today, with no preferences, Jev sent 32 of 34 items to you. With a preference entry for the 3 M1 items, its confidence went from 0.33–0.59 to 0.97–1.00 in the eval.

**In:**
- **Our own history first.** Every pick you make (`user_chosen`), every Jev pick you accept, and every quantity you set is already in SQLite. From it: for each list term, the product you chose last time and how often, plus your usual quantity.
- **The store's order history**, read through the site as a user would, in the "meus pedidos" pages. It's the fastest way to seed preferences before our own history is large ([journal 0008](journal/2026-09-30-0008-purchase-history-is-the-fastest-preferences.md)). Those pages haven't been looked at yet, so they need an M0-style session first.
- **How it reaches Jev:** the learned preference goes into the question as context, as `preferencias.yaml` does today. It also fills step B of the quantity rule (list → preference → 1 unit flagged).
- **The hand-written `preferencias.yaml` stays.** It wins over what's learned.

**Out:** recommendations ("you usually buy X, add it?"), substitutes for out-of-stock products.

**Done when** the same kind of list as M3, run again, needs clearly fewer manual picks, and no product you wouldn't have chosen is accepted without asking. The target number is set after M3's report.

**Johann has an idea for M4 and will write it up in a separate doc after M3.**

**Open, for the M4 design:** how "the same item" is recognized across lists ("leite", "leite integral", "leite ninho"). Exact term, normalized term, or Jev deciding whether a past product fits today's item.

## M5 — UX improvements ⬜

**Goal:** the improvements Johann saw while using M2. **Johann will draft a doc with ideas after M3.**

Seen so far, as a starting list, not a commitment:
- the crossed-out line ("saco lixo pia e banheiro") is sometimes read anyway; the review could flag lines with a crossed-out look;
- when Jev finds nothing that fits, no card is pre-selected, so Enter does nothing;
- items with no results ("lanche infantil") could offer a new search term right there, instead of being skipped;
- the CLI's empty Enter doesn't accept Jev's pick.
- show M3's time and corrections report on the web app's done screen, not only in the terminal (LLD-M3 Q1).

## M6 — Julia-1 as a local decide backend ⬜

**Goal:** decide without a paid API, on this machine, as an alternative to Jev.

**In:**
- Julia-1 behind the same `decide_list` contract, chosen in `config/decide.yaml`;
- the 7 resolver cases and the M3 list as the comparison;
- thresholds calibrated per backend, since alfa0 showed one 0.8/0.5 can't serve both.

**Done when** the eval reports Jev and Julia-1 side by side (raw picks right, accepted without asking, wrong products added), and the run can use either. Which one is the default is decided from those numbers.

**Known from alfa0:** Julia-1 needs PyTorch and a manual install. Its 512-token head limits how many candidates fit in one question.

## M7 — Cloud migration ⬜

**Goal:** run without this machine being on. **Design TBD.**

Constraints already known, for the design to answer:
- **The browser** has to move: into Docker, or onto a hosted browser service such as browser-use.com (Johann, LLD-M2 R1.2). The worker-thread design already keeps the browser behind one owner.
- **OCR** runs today through `claude -p` on Johann's subscription. In the cloud it needs an API key and is billed per call.
- **Access** today is a LAN token. On the internet it needs real authentication.
- **Personal data** (session cookies, purchase history, list photos) leaves this machine, so where it lives and who can read it is a design decision, not a detail.
- Out of scope in v0 per the [reset](project-reset.md). This milestone is where that changes.

## Questions resolved in review

| #   | Question            | Answer (Johann, 2026-10-01)                                                                                                                              |
| --- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | M3's baseline       | His estimate: over 1 h by hand online for 30–50+ items, 2–3 h for larger lists. In person: 2 h or more, plus checkout and loading and unloading the car. |
| 2   | M4 order of sources | Johann has an idea and will write it up in a separate doc after M3.                                                                                      |
| 3   | M5 timing           | After M3. Johann will draft a doc with ideas.                                                                                                            |
