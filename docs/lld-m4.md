# Shopping Minion — M4: preferences from purchase history

- **Status:** Draft, Part 1 (design). Rounds 1–3 answered by Johann on 2026-10-02 (§7) and folded in. T12 started at Johann's request. Part 2 (LLD and tasks) is written after Part 1 is approved.
- **Date:** 2026-10-02
- **Sources:** [ideas-m4.md](ideas-m4.md) (Johann), [roadmap.md](roadmap.md) §M4, [journal 0008](journal/2026-09-30-0008-purchase-history-is-the-fastest-preferences.md), the M3 report ([lld-m3.md](lld-m3.md) §5).
- **Why the design is in this file:** M4 adds a data source, the store's order history, so the HLD has to change (CLAUDE.md). Johann asked for that design here, in the same doc as the LLD. Once it's approved, [hld.md](hld.md) gets a short section that points here.

# Part 1 — Design

## 1. Goal

Jev picks right more often, so Johann picks less. The M3 report is the starting line. In run 8, a 32-item list:
- Jev accepted 5 products on its own and sent 25 to Johann;
- in 13 of those 25, Jev's pick was the one Johann confirmed. **Jev was right, but not sure.**
- in 11, Johann chose another product.

The store already knows what Johann buys: his past orders. M4 reads them and uses them for two things:
- to help Jev choose (Johann's mechanism, [ideas-m4.md](ideas-m4.md));
- to suggest quantities.

**Done when** (Johann, R1 Q1): on a new real list run through the web app, Jev's pick is the product Johann ends up with for **at least 95% of the items**. Put the other way, fewer than 5% are misses.
- **A hit** means the product in the final cart is Jev's top choice. That covers two cases: an item Jev accepted that stays untouched in the cart review, and an item sent to Johann where he confirmed Jev's pick.
- **A miss** means Johann chose another product, removed Jev's pick in the cart review, or skipped an item Jev had accepted.
- **Wrong accepts count as misses.** They are not a separate zero target. Johann's reasoning: a hard zero would push the thresholds up and overfit.
- **Run 8 as the baseline:** 18 hits of 29 decided items (5 accepted, 13 confirmed), so 62%.

- **The 95% applies to items with history** (R2 Q2). History can't help an item never bought, so those are reported apart, and the overall rate too.
- **The picker count has no target** (R2 Q1). It is reported. With a high hit rate, a picked item costs one Enter.

## 2. What exists today

These are facts read from the code and the history, not plans.

- **Decide:** one Jev Choice per item. The options are the search candidates plus `nenhum`, described by name, brand, price and unit of sale. The context is the item and, if there is one, its entry in `data/preferencias.yaml` (`decide.py`).
- **Quantity:** the list's quantity, then the preference, then 1 unit flagged `QUANTITY_ASSUMED` (`quantity.py`).
- **Our own history:** SQLite has, per run:
  - the candidates Jev saw;
  - Jev's answer;
  - Johann's pick, with Jev's original choice next to it in `run_log`;
  - the cart result.

  It does **not** have what was actually bought. Checkout is manual and happens outside the app.
- **The store's order pages:** not observed yet. What Johann described in ideas-m4.md:
  - the list is at `/minha-conta/pedidos`;
  - each order is at `/minha-conta/pedidos/<n>`;
  - the order page shows part of the products, and **"Ver mais produtos"** expands the full list;
  - one example line: `laranja pêra rio kg | 2,045kg | R$ 6,11`.

## 3. Flow

```mermaid
flowchart TD
    sync["0. history sync<br/>Playwright, read-only, new orders only"] --> db[("orders in SQLite")]
    ocr["1–3. photo, OCR, list review"] --> search["4. search<br/>unchanged"]
    search --> match["4.5 hist(item)<br/>history lines for this item"]
    db --> match
    match --> decide["5. decide<br/>Jev, Choice, with hist(item)"]
    decide --> qty["quantity: list, preference,<br/>history, then 1 unit"]
    qty --> pick(["picker: Jev's pick, then offers,<br/>then by probability"])

    classDef human fill:#fde68a,stroke:#b45309,color:#1f2937
    classDef model fill:#c7d2fe,stroke:#4338ca,color:#1f2937
    classDef code fill:#d1fae5,stroke:#047857,color:#1f2937
    classDef new stroke-width:3px,stroke-dasharray:4
    class pick human
    class decide model
    class sync,search,match,qty code
    class sync,match new
```

Two steps are new: **history sync** and **hist(item)**. The decide question, the quantity rule and the picker's order change. Everything else stays.

## 4. Components

### 4.1 History sync (new)

- **Read-only, through the site, as a user would.** It opens `/minha-conta/pedidos`, then each order, clicks "Ver mais produtos" and reads the lines. It clicks nothing else.
  - Order pages may have buttons such as "comprar novamente". That's an inference, to be confirmed. Those buttons would write to the cart, so a guard test forbids them in the code, the same way it forbids checkout.
- **Where the data comes from:** like the search, from the JSON the page itself receives if there is one, and from the DOM otherwise. This is decided by an observation session first (task T12, an M0-style session with Johann on his account), written down in `site-notes/andorinha.md` without personal data.
- **What the session has to answer:**
  1. Does an order line carry the product id, the same one the search uses? This decides how simple §4.3 can be.
  2. For weighed products, does it show what was ordered (2 kg) or what was weighed (2,045 kg)?
  3. How do the order list and "Ver mais produtos" load (pages, a JSON response)?
  4. What does each line show: name, quantity, unit, price, and order date?
- **Incremental and automatic:** at the start of each run, before the search, it reads only orders it hasn't stored yet. It stops at the first order id already in the database. The first sync reads the last **N orders**, with N in `config/history.yaml` and 5 to start, per ideas-m4.md. There is also a command: `shopping-minion history sync`, and `just history-sync` (R1 Q9).
- **The browser:** in the web app, the same worker thread that owns the browser for search and cart. One browser, one owner.

### 4.2 Storage (new tables)

```
orders       order_id, placed_at, total, synced_at
order_lines  order_id, line, product_id (if the page has it), name, quantity, unit, price
```

These tables hold personal data. They stay in `data/shopping-minion.sqlite`, never committed. Test fixtures are made up.

### 4.3 hist(item): the history lines for an item

Johann's idea: `hist(item) = AI(filter hist to the lines related to [item] or [busca])`. Agreed in R1 Q2: start with code, and add a model only where code measurably misses. This is the algorithm.

**Input:** the item, its candidates from the search (up to 15), and the order lines from the last N orders (§4.1).

**Normalization**, the same as `preferences.py` plus punctuation:
- `norm(text)`: accents removed (NFKD), case folded, anything that isn't a letter or a digit turned into a space, spaces collapsed. So `laranja pêra rio kg` and `Laranja Pêra Rio Kg` both become `laranja pera rio kg`.
- `words(text)`: the words of `norm(text)`, without `de`, `da`, `do`, `das`, `dos`, `com`, `e`. Numbers and units stay: `2l` and `600ml` are different products.

**Step 1: history per candidate.** For each candidate `c`, the lines that are the same product:
- **by id**, when order lines carry the product id (T12 tells): `line.product_id == c.product_id`;
- **by name** otherwise: `norm(line.name) == norm(c.name)`. This is exact equality after normalization, not a fuzzy match.

From those lines, code computes:
- in how many orders `c` appears;
- the date of the last one;
- the quantity and unit of the last one.

That's what the question shows (§4.4). A candidate with no lines gets nothing.

**Step 2: history about the item, outside the results.** These are lines no candidate matched, but where every word of `words(item.search_term)` is in `words(line.name)`.
- *Example:* "leite" matches the line `Leite Ninho Integral 1L`, which may not be in the top 15.
- This step can also catch noise: "laranja" matches a `Suco Xando Laranja` line too.
- These lines feed variant B of the question (§4.4) and the Q6 case (§4.6). They never annotate a candidate.

**Step 3: near misses, for the eval only.** A line from step 2 that looks a lot like a candidate counts as a near miss. The test is that the two share at least 60% of their words (Jaccard over `words`).
- Near misses are listed in the eval report as possible renames. They don't change any decision.
- If the eval shows many of them, that is the measured gap that would justify a fuzzy match or a model filter.

**One function.** Steps 1–2 sit behind `lines_for_item(item, candidates, lines, k)`, so BM25, or BM25 and vectors fused with RRF, can replace the exact match later without touching `decide.py` (design note, below).

**Cost:** pure functions over at most 15 candidates × a few hundred lines per item, so nothing to worry about. No model call, and deterministic, so the tests can pin every case.

**Why not a model first:**
- it would be one more call per item;
- run 8 shows the problem is confidence, not knowledge: Jev was already right 13 times out of 25;
- a history line about a product that isn't among the options can't be chosen anyway.

### 4.4 decide: the question with history

The eval runs every variant on the same items and compares them (R1 Q3).

**Variant A: history on each option.** Jev reads instructions literally and gets worse with irrelevant context (CLAUDE.md), so the history goes on the option it's about:

```
instructions:
  question: "Qual produto da loja corresponde ao `item` da lista de compras?
             Respeite as restrições de `item` e a `preferencia`. Entre os produtos que
             correspondem, prefira o que já foi comprado antes. Entre produtos equivalentes,
             prefira o que está em oferta. Se nenhum corresponde, escolha `nenhum`."
  item: {name: "laranja", source_line: "Laranja"}
criteria:
  p1234: "Laranja Pêra Rio Kg, em oferta, de R$ 6,49 por R$ 5,98, vendido por kg; comprado antes: 3 vezes, a última em 2026-09-20, 2,045 kg"
  p5678: "Laranja Bahia Kg, R$ 8,99, vendido por kg"
  p9012: "Pão De Laranja Kg, R$ 39,90, vendido por kg"
  ...
  nenhum: "Nenhum dos produtos listados corresponde ao `item` ..."
```

The ids, dates and counts above are made up, to show the shape.

**Variant B: Johann's draft.** Step 1 and step 2 lines go in the instructions as a list (`historico: ["laranja pêra rio kg | 2,045 kg | 2026-09-20", ...]`). The options stay as today.

**Variant C: history as a prior, in code (Claude's proposal).** Jev gets today's question, with no history. Code then re-weights its answer:
- **How:** each option's probability is multiplied by a weight that grows with how often that product was bought, `1 + α × min(orders, 3)`, and the result is normalized.
- **Pros:** no prompt change and no extra call. It reuses the baseline replay, and the effect of history is one number you can read.
- **Con:** α is tuned on run 8's ~30 items, which is the overfitting Johann warned about. So C is reported in the eval but is not a candidate to ship unless it clearly beats A and B.

**For all variants:**
- **The order of authority** is the list's constraints and the hand-written preference first, history after. So "leite zero lactose" on the list wins over the regular milk bought last month.
- **Code counts, Jev doesn't.** "3 vezes", the last date and the last quantity are computed in code.
- **Offers:** an option on sale reads "em oferta, de R$ X por R$ Y" (`list_price` is already in `Candidate`), and the question tells Jev to prefer offers between equivalent products (R2 Q4). This sentence is in every variant, and in the baseline replay too, so the eval compares history, not offers.
- **The thresholds stay** (0.8 / 0.5). Whether they need to move is measured in the eval, not assumed.
- **[pref]:** they stay in `preferencias.yaml` (R1 Q4).

### 4.5 Quantity

New step between preference and the default (ideas-m4.md: "the quantity bought before is a suggestion"):

1. the list's quantity;
2. the hand-written preference;
3. **the last quantity bought of the chosen product**, flagged `QUANTITY_FROM_HISTORY` so the cart review shows where it came from;
4. 1 unit, flagged `QUANTITY_ASSUMED`.

**Weighed products** (R1 Q5): the history amount is rounded to the **nearest step of the chosen product's own stepper**, not to a fixed 0,5 kg, because products use different steps.
- *Examples:* 2,045 kg with a 0,5 kg step gives 4 clicks (2 kg). With a 0,1 kg step it gives 20 clicks (2 kg).
- **At least 1 click.** Today's `to_clicks` rounds up, which is right for an amount written on the list. History uses "nearest", so `to_clicks` gets a rounding mode.
- **History in units and a product sold by kg,** or the reverse: the history isn't used, and step 4 applies.

### 4.6 Edge cases (from ideas-m4.md)

1. **Never bought:** hist(item) is empty. The question, the criteria and the quantity rule are exactly today's. No new behavior.
2. **Not in the search:** the item has no candidates. It never reaches Jev, as today (`no_match`).
3. **Bought before, but not in this search** (step 2 of §4.3 found lines, none matched a candidate): no extra search. The item is decided on today's search alone, and that history is ignored (R1 Q6, read as (b) in R2 Q3). Variant B still lists those lines; the eval shows whether that helps or hurts.

### 4.7 Measuring it: an eval before a live run

Run 8 is a ready-made eval:
- the candidates per item are saved;
- Johann's picks are in `run_log`, with Jev's original choice;
- the 5 accepted products survived the cart review untouched.

`evals/history_eval.py` replays run 8's saved candidates through Jev, once without history (the baseline) and once per variant (§4.4). It reports, per variant:
- **hit rate** (§1) on items with history, on items without, and overall, with the misses listed: item, Jev's pick, Johann's pick;
- **items accepted without asking,** and how many of those are misses;
- **items sent to the picker;**
- **items whose final product has history** but step 1 found none, plus the near misses (§4.3 step 3).

**One trap:** if Johann checked out run 8's cart, that order is in the history and contains the answers. The eval only uses orders placed **before** run 8's date.

Cost: about 30 Jev questions per replay, 3 replays (baseline, A, B; C reuses the baseline), a few cents in all. One paid job at a time.

Run 8 has ~30 items, so one miss is ~3%. The eval is for choosing between variants; the done-when is checked on a new list, live (§1).

### 4.8 UX, without blocking M5

From ideas-m4.md: the picker sorts the cards by Jev's probability, and products on offer get priority (R1 Q7). The proposal:
- Jev's pick comes first;
- then the products on offer, by probability;
- then the rest, by probability.

Offers sit at the top for quick reading, since Johann reviews every pick (R2 Q4). The decision already holds the `probabilities`, and `Candidate` holds `list_price`, so this is a small change in the picker. Two things are left to M5: a "comprado antes" badge on the card, and editing preferences in the UI.

## 5. Out of M4

- Recommendations ("you usually buy X, add it?") and substitutes for products out of stock. Per the roadmap.
- Our own runs' picks as a history source. The store's history overrides them; learning implicit preferences is for M5 (R2 Q5).
- Commodity vs personal-choice items, and how a hit is defined for an item where several products are fine (R2 Q7, design note below).
- Julia-1 as the filter or decider (M6).
- Editing preferences in the web app (M5), and preferences set by the user apart from history, e.g. "Guaraná Antarctica over Dolly" (R1 Q4; where it goes in the roadmap is R2 Q6).

## 6. Risks and unknowns

- **Not observed:** everything about the order pages (§4.1). T12 comes first, and the LLD (Part 2) is written after it, not before.
- **Step 2 noise** (§4.3): "laranja" also matches suco and Fanta lines. Only variant B and the Q6 case use step 2, and the eval shows what it costs.
- **Name match without product ids** can miss when the store renames a product. The eval counts misses.
- **History can pull Jev the wrong way:** a product bought once by mistake would be preferred. "3 vezes" vs "1 vez" in the criteria gives Jev the signal, and the cart review is the last check. The eval measures wrong accepts.
- **Personal data:** orders hold names, addresses and prices. Only product lines are stored, never the delivery address or the payment.

## 7. Questions

### Round 1 (answered by Johann, 2026-10-02)

1. **Done when.** At most 12 of ~30 items sent to the picker on a list like run 8's (half of run 8's 25), and 0 wrong accepts. Is that the right target?
> let's consider failure rate < 5% of the item count, i.e.,  hit rate >=95%. 0 wrong accepts may inadvertely cause overfiting.

2. **hist(item): code first.** Match by product id or normalized name against the item's candidates. The model filter is added only if the eval shows a real gap. OK, or do you want the model filter in M4 from the start?
> agree with caveat - let's discuss _how_ the code is gonna do that, i.e., algorithm

3. **Where the history goes in the question.** Proposal: on each candidate's description ("comprado antes: 3 vezes, a última em …, 2,045 kg"), plus a sentence in the question ("prefira o que já foi comprado antes"). The alternative is your draft: the hist(item) list in the instructions. The eval can run both, at about the same cost. Run both?
> run both and let's measure - as I said in ideas-m4, that mechanism was me just throwing what I _think_ may work here. If you have a better proposed approach, I'm all ears.

4. **[pref] in the UI.** M4 keeps `preferencias.yaml`; editing preferences in the web app goes to M5. OK?
> OK. Thinking in roadmap terms, I prefer to start exploring usage of history data and then start working on a custom mechanism to set/get user preferences unbound to history purchases (e.g: I prefer Guaraná Antartica than Dolly or whatver brand)

5. **Weighed products' quantity.** If the history says 2,045 kg, suggest 2,045 kg (rounded up to the stepper) or round to the nearest 0,5 kg?
> nearest, but there's a pitfall here that different products may use different steps

6. **A product from the history that isn't in the search results.** Leave it out in M4 (proposal), or search for it by its name and add it as an option?
> search for its name and ignore history

7. **UX in M4.** Only the picker sorted by Jev's probability. The "comprado antes" badge goes to M5. OK?
> yes, but I forgot to ask to give higher priority to products in offer/discount

8. **Our own runs as a source.** Out of M4 (§5). The roadmap's M4 text says "our own history first", so this flips it: the roadmap is updated in its own commit once you answer. OK?
> I didn't follow what you mean - explain better to me in claude chat

9. **Sync: N and when.** The first sync reads the last 5 orders. After that, at the start of every run, it reads only new orders, plus `just history` by hand. OK?
> ok; call it "just history-sync"

### Round 2

Answer inline with `>` under each one. _The answers below were given by Johann in the Claude chat on 2026-10-02 and transcribed here by Claude._

1. **Picker target.** R1 Q1 set the hit rate at 95% and said nothing about the picker count I proposed (at most 12 of ~30). Proposal: drop it. If the hit rate is 95% and confidence is still low, everything goes to the picker, but you only press Enter. The hit rate is the real goal, and the picker count is reported without a target. OK?

> agree

2. **Items never bought.** History can't help an item you never bought (edge case 1, §4.6), and the only fix there is a preference you set (R1 Q4, after M4). Proposal: report the hit rate in two parts, items with history and items without, with the 95% target on **items with history**. The overall rate is reported too. OK, or 95% over every item?

> agree: apply the 95% hit rate only to items with history.

3. **Reading "search for its name and ignore history"** (R1 Q6). Two readings:
    - **(a)** Search the store by the bought product's name, and if it's found, add it as an option, **without** the "comprado antes" note. Jev sees it as one more product.
    - **(b)** Don't search again: decide on today's search alone and ignore the history for that item.(a) costs one more search per item (~2 s), and step 2's noise can trigger it: "laranja" would search for the Xando juice bought last month. To limit that, it runs only for the line bought most often among the item's step 2 lines. Which one did you mean?

> (b)

4. **Offers.** In Jev's question, the offer is a fact on the option ("em oferta, de R$ X por R$ Y"), with no instruction to prefer it. In the picker, offers come right after Jev's pick (§4.8). Or do you want Jev told to prefer offers between equivalent products? That would be one more variant in the eval.

> I prefer that Jev be told to prefer offers. I review every pick anyway, so offers should also be at the top of the picker list, for quick reading.

5. **Our own runs** (R1 Q8). Explained in chat. Your answer here.

> The store's history overrides the local runs. M5 may improve on this with a strategy to learn the user's implicit preferences, which may be hard or impossible to extract from purchase history alone. TL;DR: out of M4 for now, since it's already handled in other points.

6. **User-set preferences, apart from history** (R1 Q4: "Guaraná Antarctica over Dolly"). In the roadmap, as its own milestone right after M4, or as an item in M5? The roadmap changes in a commit of its own once you answer.

> Connected to Q5.
> 
> _Claude's note: the answer doesn't say whether this is its own milestone or an item in M5, so the roadmap commit is still pending._

7. **Commodity vs. personal-choice items** (added in chat on 2026-10-02, from Johann's framing of the problem). Some items are commodities where several products are equally good (ground beef: patinho, paleta, whatever is on offer), and some are personal taste (beer). Idea: an "open" item accepts a set of products and a code rule picks inside the set; a "fixed" item has one product. History could infer which one it is, by counting the distinct products bought per item. Two sub-decisions came up: the default rule inside an open set (cheapest per kg, offer first, or bought before if on offer), and the definition of a hit for open items (§1 says "Jev's top choice", which is unfair when several products are acceptable).

> Connected to Q5.
> 
> _Claude's note: the two sub-decisions are not decided. They move to the M5 work on implicit preferences, together with this item._

### Round 3

1. **Where user-set and implicit preferences go in the roadmap** (R2 Q6, "connected to Q5"). Proposal: M5 gets a second part, "Preferences beyond history", with:
   - user-set preferences ("Guaraná Antarctica over Dolly", edited in the web app);
   - implicit preferences learned from the app's own runs;
   - the commodity vs personal-choice split, with its two open sub-decisions (R2 Q7).

   M5 stays one milestone, with UX and preferences as its two parts. The other option is a milestone of its own between M4 and M5. Which one?

> Preferences go with M5. Johann will elaborate other UX changes there that partly fit with this preferences question. _(Answered by Johann in the Claude chat on 2026-10-02 and transcribed by Claude.)_

## Design note: where the problem is, and where retrieval fits (chat of 2026-10-02)

_Drafted by Claude from Johann's chat, for his review._

**The problem.** Items sit on a spectrum. At one end are commodities, where several products are equally good (ground beef: patinho, paleta, whatever is on offer). At the other end is personal choice (beer: one product, one taste). Jev answers "which product?", and for a commodity the right answer is a set, so its confidence spreads across equally valid options. *Hypothesis, not measured:* this may explain the 13 of 25 items in run 8 where Jev was right but not sure. To check, classify the non-hits of run 8 as commodity or personal.

**What M4 does, and what it leaves out**
- History is evidence for Jev, not a new decider: variants A, B and C stay in the eval (§4.4). Jev is told to prefer offers between equivalent products (R2 Q4), and offers also sort to the top of the picker.
- Out of M4: the commodity/personal split, preferences learned beyond purchase history, and our own runs as a source (R2 Q5–Q7). They go to M5, together with the open sub-decisions of Q7.
- No knowledge graph. "What did I buy last time for this item?" is a one-hop lookup. Revisit only if the eval's near-miss count (§4.3, step 3) shows a gap that a lookup can't close.

**Where BM25 and RRF fit**
- Variant A needs neither. It joins history to candidates by product id or exact normalized name.
- Variant B has a real retrieval problem: choosing which history lines go into the instructions, within a budget, because Jev gets worse with irrelevant context. BM25 ranks the lines by relevance to the item. RRF fuses that rank with recency and frequency, with no weights to tune (the problem of variant C's α). This only matters if B survives the eval.
- If the eval shows many renamed products (near misses), the same pair is the upgrade for the name match in step 1.
- Limit: at M4's scale (a few orders, short item names) BM25 behaves almost like "all words match". The gain is ordering, and it is small.
- Vector search only helps where words don't overlap ("carne moída" ↔ "patinho moído"). That is the M5 commodity problem, and where BM25 + vectors fused by RRF would be the natural design.

**Architecture hook.** Keep the match behind one function with a stable signature, for example `lines_for_item(item, candidates, lines, k)`. It runs in pure Python over lines loaded from SQLite, not inside the database. That lets exact match, then BM25, then BM25 + vectors with RRF replace each other without touching `decide.py`, and it keeps the code portable if the app moves to the cloud.

**Next:** T12, the observation session on the order pages. Part 2 is written after it.

# Part 2 — LLD

Written after Part 1 is approved and the order pages are observed (T12): contracts, the `orders` tables, the sync module, the eval, the question text, and the task breakdown in waves.