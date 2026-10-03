# Shopping Minion — LLD M5: UX and preferences

- **Status:** Draft, for Johann's review.
- **Date:** 2026-10-02
- **Implements:** [hld-m5.md](hld-m5.md), as answered on 2026-10-02. **Done when** (HLD §2): on a real list of 40+ items through the web app:
  1. the app's check finds every decided product in the cart, or names each failure;
  2. Jev's pick is the final product for ≥ 90% of items with history;
  3. Johann's time per item is about half of run 10's (≤ ~22 s).
- **Already done** (HLD answers): `accept_at` is 0.75 (`16b8a6a`).

## 1. Part A — a cart that doesn't need rechecking

### 1.1 Empty searches (`search.py`)

- A search that comes back with 0 candidates is opened once more (`page.goto` again, same URL) before it counts as empty. That is what a user does with an empty page. Run 11 had four such searches, and later reads of the same terms found 12–15 products.
- The `search` row in `run_log` (M4) gets `"retried": true` when it took a second read.
- An item still empty after the retry goes to the picker, not to `skipped`. Its card says "nada encontrado para <termo>" and has a field for a new search term (§2.3).

### 1.2 An add the site didn't take (`cart.py`)

What is known (runs 10 and 11, água sanitária 5 L and alface): after the click on "Adicionar", the buy box still shows the button and no stepper. The cause is unknown (HLD Q2, still open). Finding it needs a click, which only Johann makes.

So M5 makes this case recognizable and leaves a trace:
- **Message:** if the add button is still visible 3 s after its click and no stepper showed, the result is `failed` with "o site não aceitou o clique em Adicionar".
- **Screenshot:** the buy box is saved to `data/logs/add-<product_id>-<time>.png` (personal, never committed), so the first real case shows what the page displayed.
- **Retry:** the run goes on; the line can be retried from the done screen (§1.3).

### 1.3 The done screen names every failure, and retries them

- **The outcome screen lists** every line that isn't ok: product, expected quantity, what the cart shows, and the message (`CartResult.message` or the check's verdict). It is already in `CartOutcome`; today it isn't shown in full.
- **"Tentar de novo" for the failed lines.** `POST /api/run/retry`, allowed in `done`:
  - it builds a draft of the lines that failed or didn't check;
  - it moves to `filling_cart` and runs the same `fill_cart` on that draft;
  - it comes back to `done` with a new check, logged as another `check` row.
- **Checkout stays manual.** Nothing here goes near it.

### 1.4 The phone losing its connection (`web/app.py`, `cli.py`, `app.js`)

- **The access cookie** gets `max_age` of 30 days, so it isn't a session cookie the phone's browser drops.
- **The token** is kept in `data/web-token` and reused across server starts. `shopping-minion serve --new-token` makes a new one. A rescan is then needed only after `--new-token`.
- **The page** closes and reopens its event stream when it comes back to the foreground (`visibilitychange`), and then reloads the snapshot. A banner says "reconectando…" only while it is actually disconnected.

## 2. Part B — Johann's time

### 2.1 Several photos (`intake.py`, `web/app.py`, `storage.py`)

- `POST /api/run` takes 1 to 5 files (field `photos`). Above 5 is a 400.
- `transcribe(photos: list[Path])` copies every photo to the temp dir and names them all in the request. The order is the order of upload.
- The prompt gets a rule: "the images are pages of one list, in order; `source_line` stays per line; return items in page order".
- **The JSON Schema doesn't change.**
- `runs.photo` keeps a JSON list of paths, and a single path is still read for old runs. `GET /api/run/photo?i=<n>` serves each photo, and the review screen shows them as tabs.
- **Check before merging:** `intake_eval.py` on `list-001`, and on today's 3 pages read as one call against the 3 separate reads. These are the same `claude -p` calls on Johann's subscription; Claude says so before running them.

### 2.2 The review card (`index.html`, `app.js`)

- **Shown by default:** "linha" (`source_line`, read-only) and "busca" (`search_term`, editable). Name, constraints and brand go behind a "mais" toggle per card.
- **Quantity:** shown when it isn't null. An "+ quantidade" link adds it otherwise.
- **A line with several items** (same `source_line`, consecutive): one card, with one "busca" field per item, grouped. Deleting one field deletes that item. Today they are separate cards with the line repeated.
- The `Item` contract doesn't change. This is presentation only.

### 2.3 One pass: product and quantity (`statemachine.py`, `run.py`, `workflow.py`, `web/app.py`, `index.html`, `app.js`)

**The picking screen:**
- **Fixed header:** the item and its search term stay on top (`position: sticky`) while the cards scroll.
- **Quantity under the selected card,** prefilled with the quantity the draft would use for that product (list → preference → history → 1 un, flagged). It is editable in place, so one confirmation sets product and quantity.
- **For the picker to show it:** `PickView` gets `quantities: {product_id: {value, unit, flags}}` for every candidate, computed by the same `target_quantity` with that candidate's history.
- **`PickBody`** gets `quantity: Quantity | None`. When given, it replaces the target for that item (no flag; `cart_edit`-style log row kind `pick_quantity`).
- **No results** (§1.1): the card shows a search field. `POST /api/run/picks/search {index, term}` runs one search for that item in the worker and sends the new candidates back. Then the pick goes on as usual.

**After the last pick, the end of the pass:**
- **The summary screen**, which is the old `reviewing_cart` state with a new layout, shows:
  - **one line per accepted item, collapsed:** product, quantity and badges;
  - **the estimated total;**
  - **"Adicionar ao carrinho"**, which confirms;
  - **"Revisar tudo"**, which opens today's full editor, where any line can change quantity or be removed.
- **Going back to an item already picked:** each line has "trocar", which reopens that item's cards. That is `POST /api/run/picks/reopen {index}`, back to `picking` for that one item.
- **The states don't change.** The report keeps counting `picking` and `reviewing_cart` as Johann's time.

### 2.4 Badges (`index.html`, `style.css`)

| Flag | Text | Color |
|---|---|---|
| `QUANTITY_ASSUMED` | quantidade assumida | amber |
| `QUANTITY_FROM_HISTORY` | mesma quantidade da última vez que comprou este produto | blue |
| `QUANTITY_INEXACT` | aproximada | gray |
| on offer (`list_price > price`) | oferta | green |

Each color is defined once as a token, in light and dark.

### 2.5 The report on the done screen

`GET /api/run/report` returns the same numbers `report.py` prints, as JSON, and the done screen shows them under the outcome: time per step, total, and corrections. `report.py` is split into a pure `build_report(storage, run_id) -> dict` and its text printer.

## 3. Part C — search terms for meat and produce

### 3.1 The OCR prompt (`prompts/intake.md`)

New rules, each with an example from runs 9–11:
- **Meat:** `search_term` is the cut, not the dish. "carne de panela (acém ou paleta)" gives the searches "acém" and "paleta"; "carne moída paleta" gives "paleta moída"; "frango coxa e sobrecoxa" gives "coxa e sobrecoxa".
- **"A ou B" written for one item:** `search_term` is A, and the new field `alternatives` is `["B"]`. The schema gets `alternatives: string[]` (default `[]`), and so does `Item`.
- Every prompt change is run on `list-001` and on today's pages, and the result goes in the commit message ([hld.md](hld.md) §3.1).

### 3.2 Synonyms (`config/search_terms.yaml`, `search.py`)

```yaml
salsinha: salsa
```

- **Lookup:** before searching, the normalized `search_term` (and each alternative) is looked up. A hit replaces the term.
- **Where it shows:** the `search` row logs both terms.
- **How it grows:** the file starts with what failed live, and grows the same way.

### 3.3 Alternatives in the search (`search.py`, `workflow.py`)

`search_list` searches `search_term` and each alternative. The candidates are merged in order, deduplicated by `product_id`, up to 15. Jev and the picker see one list. Search time grows by one search per alternative.

## 4. Part D — preferences that learn

### 4.1 Learned from picks (`learned.py`, `decide.py`)

- **Source:** the final product of each item in runs ≥ `learn_from_run` (`config/history.yaml`, `9`: Johann, HLD Q6). That is:
  - `choice` of `accepted` and `user_chosen` decisions;
  - minus lines removed in a `cart_edit`;
  - never the current run.
- **Key:** `norm(item.name)` (`history.norm`).
- **Per product:** `LearnedPreference {product_id, times, last_at}`.
- **In the question:** like M4's variant A, a fact on the option written by code: `; escolhido por você 3 vezes, a última em 02/10/2026`. When a product also has order history, both facts appear. The question's sentence becomes "Entre os produtos que correspondem, prefira o que você escolheu ou comprou antes."
- **Off switch:** `config/decide.yaml` gets `learned: true | false`. The `decide` row in `run_log` records it.

### 4.2 Set by Johann, in the web app (`storage.py`, `web/app.py`, `index.html`, `app.js`, `preferences.py`)

- **Storage:** a `preferences` table: `key` (normalized item name) and `entry_json`, with the same fields as `preferencias.yaml` (`apelidos`, `variante`, `marca`, `excluir`, `quantidade`).
- **First import:** `data/preferencias.yaml` is imported once on first start, if the table is empty. After that, the table is the source, and the file is left alone.
- **Screen "Preferências"** on the idle screen: a list with add, edit and remove. API: `GET/PUT/DELETE /api/preferences[/{key}]`.
- **No code change in decide:** `load_preferences` reads the table instead of the file, so `find_preference` and decide stay as they are.

## 5. Tasks

| Task | What | Who | Wave |
|---|---|---|---|
| **T19** | §1.1, §1.2 and §1.3: search retry, the add the site didn't take with its screenshot, failures on the done screen, "tentar de novo". Tests: fake pages (an empty first read, an add that doesn't take), the retry flow in the state machine, the done screen in the UI stub. | Sonnet | 1 |
| **T20** | §1.4: the cookie, the token across restarts, `--new-token`, reconnect on visibility. Tests: cookie `max_age`, the token reused and rotated, the UI stub reconnecting. | Sonnet | 1 |
| **T21** | §2.1 and §2.2: several photos and the review card. Tests: the upload limit, the command naming every photo, the old single-photo runs, the grouped card in the UI stub. Then Claude runs the intake eval (§2.1). | Sonnet, then Claude | 2 |
| **T22** | §2.3, §2.4 and §2.5: the one pass, badges, and the report on the done screen. Tests: quantities in `PickView`, a pick with a quantity, reopen, the search from the picker, the summary and "revisar tudo" in the UI stub, `build_report`. | Sonnet | 3 |
| **T23** | §3: the prompt rules, `alternatives`, synonyms, and the merged search. Tests: the merge and dedup, synonym lookup, the `Item` default. Then Claude runs the intake eval on `list-001` and today's pages. | Sonnet, then Claude | 4 |
| **T24** | §4.1: learned preferences in decide. Tests: what counts (runs ≥ 9, removed lines, never the current run), the option text, the switch. | Sonnet | 5 |
| **T25** | §4.2: preferences in SQLite and their screen. Tests: the one-time import, the API, `load_preferences` from the table. | Sonnet | 5 |
| **T26** | **Acceptance:** Johann runs a real list of 40+ items. Claude reports the check, the hit rate (`history_eval --live-only`), Johann's time per item, and `confidence_report`. | Johann + Claude | 6 |

**Waves:**
- T19 and T20 touch different parts of `app.py` and `app.js`, so they run in parallel.
- T21, T22 and T23 run one after another, because they all touch the review and picking screens or the OCR.
- T24 and T25 touch different files, so they run in parallel.

Each wave is reviewed by Johann before merge, as before. Part A goes first, so every later run is measured on a cart that works.

**Paid calls:**
- T21 and T23 run `claude -p` OCR evals on Johann's subscription, a few calls each.
- T24's tests use a fake client.
- T26 is a live run.

## 6. Open

- **HLD Q2:** what the store's page shows when "Adicionar" doesn't take. The screenshot of §1.2 should answer it on the next real case.
- **Open vs fixed items:** out of M5 until more piloting (HLD Q7).
