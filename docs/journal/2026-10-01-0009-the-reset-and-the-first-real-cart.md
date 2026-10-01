# 2026-10-01 — The reset, and the first real cart

_Drafted by Claude, reviewed and approved by Johann on 2026-10-01. The numbers come from the runs of that day._

Yesterday the project stopped because I hit my Claude plan's limit. I could have switched models and kept going. Instead I stopped and looked at what we had: two days, 80 commits, 13 ADRs, an HLD, an LLD, about 4,200 lines of code and 321 passing tests. **And not one item in a real cart.**

Today, about a day later, a photo of my handwritten list went through OCR, review, search, decision and cart, and ended with 31 products in my real Andorinha cart. This entry is about what changed between those two points.

## What was wrong

The first version, now in [`alfa0/`](../../alfa0/), was a well-built system that didn't do the job. Site profiles, a discovery agent to write them, a small language of browser steps, field mappings with conditions, a LangGraph graph for a sequence that never branched. Each piece made sense. Together, they put four layers between "search for atum" and the store, for a project with one store.

The discovery agent is the clearest case. It was meant to save me from writing a scraper by hand. After two supervised runs it still hadn't produced a profile. The second one spent its steps hunting for product page URLs and never tried a search. Writing the scraper by hand took one session.

The other mistake was in my own mental model: one loop per item, doing search → decide → add for each one. The prototype I built with my son did it differently. It ran each step over the whole list before the next one. That is faster end to end, and it lets the decision step see items in batches.

The full list of what we kept and what we dropped is in [lessons-learned.md](../lessons-learned.md). The principle I took from it: **complexity has to be earned before it's introduced.**

## The reset

I wrote the [reset plan](../project-reset.md) myself, in Portuguese, in Obsidian, and Claude moved everything except this journal into `alfa0/`. Then came the usual loop, only shorter:

1. Claude interviews me in a markdown file;
2. I answer inline in Obsidian;
3. it writes the HLD;
4. I comment inline and approve it;
5. the LLD follows the same way.

The design is three passes over the list:

- **search:** plain Python driving the site with Playwright, with the window open, reading the JSON the search page itself receives;
- **decide:** [Jev](https://docs.typesafe.ai/introduction), TypeSafe's "System One" model, picks a product per item with a calibrated confidence. Confident picks are accepted, the rest come to me;
- **add_cart:** plain Python clicks "Adicionar" and `+` on each product page.

No model touches the browser or the cart. Checkout is always mine.

## What the site taught us

Most of the day's learning came from looking at the real site instead of reasoning about it.

- **The product page is a link the site gives away.** Search cards carry no product id in the DOM, and alfa0 never found a stable product URL. But the search page publishes `/produtos/<id>/<slug>` for every result in its structured data, and that page has the add button.
- **The first live cart run failed three ways, all visible only on the real page:**
  - the buy box the code looked for was a hidden sticky summary;
  - the quantity counter animates, so for a moment it reads "1 2 3";
  - the Peso/Unidade (weight/unit) switch also looks like a stepper.
- **The screen lies for half a second.** On my logged-in account, the code reported papel higiênico as added, and the cart didn't have it. Each click makes the page send an `UpdateCart` to the server about 0.6 s later. The stepper changes before that. The code was moving on before the save. Now an item only counts once the page's own `UpdateCart` comes back OK, and the final report reloads the page before reading the cart.
- **The report has to check, not list.** On the next run the report printed the cart but compared nothing. Now it checks every planned product, by name and quantity, against the reloaded cart and flags anything else in it. I left an Enxaguante Bucal Colgate Plax Kids Minions in the cart on purpose, and the report caught it as "not from this list".

## The first end-to-end run

The `list-001` photo, the same one from the first day:

- **OCR:** Sonnet through `claude -p` read 34 items in 24 s. In the eval it was clearly better than Haiku: 31/32 against 26/32, and 22 s against 160 s. Sonnet runs on my subscription too.
- **Search:** 34 searches, 15 results each, except where the store had fewer. "Lanche infantil" had none.
- **Decide:** Jev accepted 2 items on its own: atum and papel higiênico, the only two with test preferences. It sent the other 32 to me. Its picks were mostly sensible, but with no preferences, many products are equally good for "arroz", and (my reading) the probability spreads across them and confidence stays low. Preferences are the lever: with them, the three M1 items went from 0.33–0.59 to 0.97–1.00 in the eval.
- **Cart:** 31 products, checked one by one against the reloaded cart: 31 of 31.

One defect got past that check. Requeijão is on the list twice, and both lines chose the same product. The second line found it already in the cart, left it alone, and the report said "ok" for both. The cart had 1 where the list meant 2. The fix is to add up quantities when two lines point to the same product.

## What's next

- The web app: upload, review, progress bars, and the one-at-a-time picking screen, replacing the terminal.
- Preferences from purchase history. That was already the conclusion of [entry 0008](2026-09-30-0008-purchase-history-is-the-fastest-preferences.md), and today's run is the evidence: 32 manual picks for a 34-item list.
- Merging duplicate list lines that point to the same product.
