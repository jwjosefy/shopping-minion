# 2026-10-03 — Ninety items, twelve minutes of mine

_Drafted by Claude on 2026-10-03, at Johann's request, from the runs and commits of 2026-10-02 and 2026-10-03. The numbers come from `shopping-minion report`, `evals/history_eval.py` and `evals/confidence_report.py`._

[Entry 0010](2026-10-01-0010-eight-minutes.md) ended with M3: a 32-item list in 8 minutes, and Jev sending me 25 of 32 items to pick. This entry covers M4 and M5, built in a day and a half. My real shopping list for the week, three handwritten pages, was the test.

## What was delivered

| | Run 8 (M3) | Runs 9–11 (M4) | Run 12 (M5) |
|---|---|---|---|
| Items | 32 | 88, in 3 runs | 90, in 1 run |
| Jev's pick was the final product (items bought before) | 62% overall | 85% | **94%** |
| Accepted by Jev alone, wrong | 5, 0 | 48, 0 | **55, 0** |
| Products that failed to go in the cart | 0 | 12 | **0** |
| My time | 5 min 15 s | 33 min | **12 min 43 s** (8.5 s per item) |

**M4, purchase history.**
- **Sync:** the app reads my last 10 orders through the store's own pages, as I would. It never clicks, and stores them in SQLite.
- **What Jev sees:** a line on each option, such as "comprado antes: 3 vezes, a última em 20/09/2026, 2,045 kg".
- **Quantity:** my last quantity of that product fills in what the list leaves out.

**M5, my time and preferences.**
- **The list:** up to 5 photos in one OCR. The review card shows only what was read and what will be searched.
- **One pass:** product and quantity on one screen, then a summary where any item can be changed.
- **Meat and produce:** searched by cut ("acém ou paleta" becomes two searches).
- **Learned preferences:** Jev also sees "escolhido por você N vezes", learned from my own picks.
- **A cart that answers for itself:** it retries an empty search, names every line that failed, and retries only those.

## What made the difference

- **The order line already carries the store's product id.** My idea doc had an AI step to filter the history for each item. When we looked at the order pages, each line had the same id the search uses. So matching history to the search results is a lookup. No model was needed.
- **Real purchases, not test runs.** Before M4 went live, Claude replayed run 8 to choose how to show history to Jev. The replay said the offer sentence cost 7 items and the history variants made Jev confidently wrong. I stopped that line: runs 1–8 were tests that the system stands up, not lists I would buy. The live runs on my real list showed 48 accepts and 0 wrong. **An eval is only as good as the purchases behind it.**
- **Measure the model's confidence, don't guess a threshold.**
  - The 0.8 threshold was invented on day one. Cataloging every Jev answer showed two clusters: below ~0.6 and above ~0.9, with the misses at 0.73 or less.
  - We moved to 0.75. On run 12 every miss was at 0.55 or less, and 0.6–0.8 was 18 hits out of 19.
- **My time was the bottleneck, not Jev.**
  - Jev decides 90 items in 6 seconds.
  - In run 10, I spent 22 minutes on 30 items, reviewing five fields per card and going through the list twice: products, then quantities.
  - One pass and a two-field card took that to 8.5 s per item.

## Problems found and how they were solved

Almost every problem was the store's site behaving in a way we hadn't seen. Each was found by reading the page or the logs, never by guessing:

1. **Above 1 kg the site writes `1.5kg`, with a dot.**
   - **Effect:** the code only read commas. Every kg product that crossed 1 kg timed out as "a quantidade não mudou", although the clicks had gone through.
   - **Fix:** read both.
2. **A produce page has four Peso/Unidade switches:** one for the product, three on suggested products.
   - **Effect:** the code looked for one on the whole page, so five fruits failed with a Playwright error.
   - **Fix:** read the switch inside the buy box.
3. **Bulk spices start at a minimum above their step.**
   - **Effect:** the first click puts 150 g and each `+` adds 50 g, so 300 g came out as 400 g.
   - **Fix:** the search already sends `min`; the code now uses it.
4. **Four searches came back empty once, then found 12–15 products later.** The likely cause is a hiccup on the site; it wasn't verified.
   - **Fix:** an empty search is now opened once more.
   - **Run 12:** 0 empty searches out of 90.
5. **"carne de panela" returned a Maggi soup.**
   - **Cause:** the OCR read "acém ou paleta" and dropped it into a constraint.
   - **Fix:** meat is now searched by cut, and "A ou B" means two searches.
   - **A new rule that failed first:** "paleta moída" found nothing, because the store names it "Carne Moída …". We fixed the rule after looking.
6. **There were no logs worth the name.** A failed run left no trace of what each search returned or which line failed the check.
   - **Fix:** a daily log file, a row per search, and the failing lines in the check.
7. **On the phone, a screen lock lost the session.** The cookie was a session cookie, and the token changed on every restart.
   - **Fix:** a 30-day cookie and a token kept across restarts.
8. **The browser window.** It now runs headless, except for the login, where I type.

## Lessons

- **Look before you build.** A short read-only look at a page answered what a day of design couldn't: the product id on the order lines, the dot in `1.5kg`, the minimum on bulk spices.
- **A cart that names its failures matters more than a smarter model.** Run 11 had no Jev mistake and only 4 of 10 products in the cart. Every failure sends me back to the site, which is what this project exists to avoid.
- **Delegating means letting it run.** I told Claude to build everything up to the live test. It stopped halfway on a rule it had written for itself, and I had to say so. For M5 it ran five waves of Sonnet tasks on its own, each merged with its own commit so any of them can be reverted, and stopped only where I'd asked.
- **Some misses aren't the model's.** I alternate between brands: Bauducco or Nutrella, Sadia or Seara. Both are in my orders, so history can't tell which one I want this time. The remaining misses are mostly that, plus one Jev keeps getting wrong: it answers "nenhum" to Toddy 700 g, a product I buy.

## What's next

- **The Toddy "nenhum".** Jev rejects a product I bought and picked before, and we don't know why.
- **"Open" items:** for some items, any of several products is fine (meat, produce). I want to pilot more before designing it.
- **Then the roadmap:** M6, Julia-1 as a local alternative to Jev, and M7, the cloud, with telemetry and CI/CD.
