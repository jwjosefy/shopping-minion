# Shopping Minion — Low-Level Design (M0 and M1)

- **Status:** Approved by Johann on 2026-10-01
- **Date:** 2026-10-01
- **Implements:** [hld.md](hld.md) (approved 2026-10-01)

**Scope:** this LLD covers M0 (looking at the site) and M1 (CLI, 3 items, real cart). M2 (web app) and M3 (full list) get their own LLD after M1 works, because M1 will change what we know about the site. That's the "checkpoint at route changes" lesson.

## 1. Layout

```
pyproject.toml                 uv, Python 3.12; deps: playwright, typesafe-sdk, pydantic, pyyaml
config/
  decide.yaml                  Jev model, batch size, confidence thresholds
  preferencias.exemplo.yaml    example; the real file is data/preferencias.yaml (git-ignored)
src/shopping_minion/
  items.py                     Pydantic contracts (§2)
  config.py                    loads config/decide.yaml
  preferences.py               loads data/preferencias.yaml
  quantity.py                  rule A→B→C and conversion to the unit of sale
  storage.py                   SQLite in data/shopping-minion.sqlite
  intake.py                    claude -p wrapper
  prompts/intake.md            OCR prompt (approved)
  browser.py                   Playwright launch, user agent, session in .auth/
  search.py                    search pass (Andorinha-specific)
  decide.py                    Jev calls and the confidence policy
  cart.py                      add_cart pass (Andorinha-specific)
  cli.py                       commands: login, ocr, run
evals/
  intake_eval.py               OCR vs evals/fixtures/list-001.yaml
  decide_eval.py               Jev vs evals/fixtures/resolver-cases.yaml
tests/                         offline, pytest; `live` marker for anything that opens the site
docs/site-notes/andorinha.md   what M0 observed (written in T0)
```

There are no interfaces, adapters or base classes. `search.py` and `cart.py` are written for Andorinha. FastAPI and uvicorn come in with M2.

## 2. Contracts (`items.py`)

```python
class Quantity(BaseModel):
    value: float            # > 0
    unit: Literal["un", "g", "kg", "ml", "l", "pct", "cx", "lata", "dz"]

class Item(BaseModel):                     # one line item, after OCR and review
    source_line: str
    name: str
    search_term: str
    constraints: list[str] = []
    brand: str | None = None
    quantity: Quantity | None = None
    needs_review: bool = False

class Candidate(BaseModel):                # one search result (field sources: §7.1)
    product_id: str
    slug: str                              # for /produtos/<id>/<slug> (§7.2)
    name: str
    brand: str | None
    price: Decimal | None
    list_price: Decimal | None             # before discount, if any
    unit_of_sale: Literal["un", "kg"]
    step_kg: float | None                  # stepper increment for kg products
    available: bool

class Decision(BaseModel):
    item: Item
    candidates: list[Candidate]
    choice: str | None                     # product_id, or None for "nenhum"
    confidence: float | None               # Jev's answer; None when the user chose
    probabilities: dict[str, float] = {}
    status: Literal["accepted", "ask", "no_match", "user_chosen", "skipped"]

class CartTarget(BaseModel):
    product_id: str
    clicks: int                            # adds/+ clicks, ≥ 1
    flags: list[Literal["QUANTITY_ASSUMED", "QUANTITY_INEXACT"]] = []

class CartResult(BaseModel):
    product_id: str
    status: Literal["added", "failed"]
    quantity_shown: str | None             # as the cart displays it
    message: str | None
```

The `Candidate` fields are the HLD's list. T0 may show that a field isn't available, or that one is missing. Then T0 updates this section, and Johann reviews the change before the tasks that use it start.

## 3. Components

### 3.1 intake

- Command:

  ```
  claude -p "<request: transcribe the list in the file <absolute path>>" \
    --model sonnet --system-prompt "<prompts/intake.md>" \
    --tools Read --json-schema '<schema>' --output-format json \
    --no-session-persistence
  ```

  It runs from a temporary directory that holds only a copy of the photo.
- **JSON Schema:** written by hand in `intake.py`, mirroring `Item`, as `{"items": [Item...]}`. Every property is required, nullable fields are typed `["string","null"]`, and every object has `additionalProperties: false`, as structured outputs require. A test checks that the schema and `Item` stay in sync: every schema property is an `Item` field and vice versa.
- **Unknown, to settle while building:** where `--output-format json` puts the structured result in its envelope. The task's first step is one real call on a small test image, and the code follows what that call returns.
- Errors: a non-zero exit, output that isn't JSON, or a result that doesn't validate raise `IntakeError`, with stderr included in the message.
- `shopping-minion ocr <photo> [-o lista.yaml]` writes the items as YAML. This is the M1 review step: Johann edits the file by hand.

### 3.2 browser

- `open_browser() -> (browser, context)`, a context manager:
  - Chromium with `headless=False`.
  - The user agent is the browser's own, read once from a blank page, with `HeadlessChrome` replaced by `Chrome`.
  - Viewport 1366×900, locale `pt-BR`.
  - `storage_state` is loaded from `.auth/andorinha.json` when the file exists.
- `shopping-minion login`:
  1. opens the home page;
  2. waits for Johann to log in by hand and press Enter in the terminal;
  3. checks that the page shows a logged-in marker (from T0);
  4. saves `.auth/andorinha.json`.
- `ensure_logged_in(page)` is called at the start of `run`. If the marker is missing, the run stops and says to run `login`. In M1 the run doesn't pause and wait.
- Nothing in this module or the others makes a request by hand: no `page.request`, no `fetch`, no HTTP client. A test greps `src/` for these names.

### 3.3 search

> See §7.1 (field mapping) and §7.7 (two pages of 12).


- For each item, in order:
  1. go to `/busca/<search_term>`, URL-encoded;
  2. collect the search JSON responses the page receives (`page.on("response")`, with the URL path pattern from T0);
  3. wait until the result count shows on the page (or "0 itens");
  4. turn the hits into `Candidate` objects, keeping the first 15.
- Dismisses the cookie banner once per session, if it is there.
- An item with 0 results gets an empty candidate list, and decide marks it `no_match` without calling Jev.
- Mapping from response fields to `Candidate` fields: defined in T0, in `docs/site-notes/andorinha.md`. Offline tests run on 3 recorded responses saved in `tests/fixtures/` (public catalog data, as in alfa0).

### 3.4 decide

- `config/decide.yaml`:

  ```yaml
  model: jev-latest        # jev-1.13 can't be pinned (API: 400 Unknown model); each Decision records the served version
  batch_size: 5
  accept_at: 0.8           # placeholder, calibrate on the 7 cases
  ask_below: 0.5           # placeholder
  ```

- One Choice per item. Questions are keyed `item_<n>`. Each question carries everything it needs, and the shared `state` is empty or nearly so:
  - `instructions` is an object: the question text, the item (`name`, `constraints`, `brand`, `source_line`) and the preferences entry if there is one.
  - `criteria`: `p<product_id>` maps to a one-line description (name, brand, price, "vendido por kg"/"por unidade", "indisponível" when out of stock), plus `nenhum` with a description of when it's right.
  - Question text, first version: "Qual produto da loja corresponde ao item da lista de compras? Respeite as restrições; se nenhum produto corresponde, escolha `nenhum`."
- Policy, with thresholds from the config:
  - `confidence ≥ accept_at` with a product: `accepted`;
  - `confidence ≥ accept_at` with `nenhum`: `no_match`;
  - between the two thresholds: `ask`;
  - below `ask_below`: `ask`, flagged "nothing fit". The CLI shows the flag.
- In M1, `ask` and `no_match` are resolved in the terminal: the CLI lists the candidates, Jev's pick first, and Johann types a number, or `0` to skip the item.
- Fallback per HLD: if five questions per call measure worse than one per call on the 7 cases, `batch_size: 1`, with the calls run in parallel. `decide_eval.py` reports both.

### 3.5 quantity

- Target: the item's `quantity`, else the preferences entry's `quantidade`, else 1 `un` with `QUANTITY_ASSUMED`.
- Conversion to clicks:
  - **`un` product:**
    - target in `un`/`pct`/`cx`/`lata` → `clicks = value`;
    - target `dz` → `value × 12`;
    - target in weight or volume → 1 click with `QUANTITY_INEXACT`.
  - **`kg` product:**
    - target in g/kg → `clicks = ceil(target_kg / step_kg)`, with `QUANTITY_INEXACT` when that isn't exact;
    - target in `un` → 1 click with `QUANTITY_INEXACT`.
- Pure functions, unit-tested. No pack-size parsing in M1.

### 3.6 preferences

`data/preferencias.yaml`, in the HLD's format:

```yaml
<termo>:
  apelidos: [...]
  marca: ...
  variante: ...
  excluir: [...]
  quantidade: { valor: ..., unidade: ... }
  # plus free fields such as fatiado
```

An item matches an entry when its `name` or `search_term` equals the key or one of the `apelidos` (accent- and case-insensitive). The whole entry goes to Jev as context.

### 3.7 cart

> Superseded in part by §7.2–7.6: cart works on the product page, not the search card.


- For each `CartTarget`, one at a time:
  1. go to `/busca/<product name>`;
  2. find the card by product id (attribute or link, from T0);
  3. click `+` until there have been `clicks` clicks in total.
- After each click, wait until the quantity shown on the card changes, with a 5 s timeout. If it doesn't change, stop and mark the item `failed`.
- **Before adding:** if the card already shows a quantity (the product is in the cart), mark the item `failed` with "já está no carrinho" and don't touch it.
- At the end, open the cart page and read each product's quantity there for the report. The browser stays open until Johann presses Enter in the terminal.
- Never in parallel. No code path reaches checkout. A test greps `cart.py` for checkout selectors and text, using the list from T0.

### 3.8 storage

SQLite, standard library only. One table per stage, holding JSON from the contracts:

| Table | Key | Columns |
|---|---|---|
| `runs` | `id` | `created_at`, `photo`, `status` |
| `items` | `run_id`, `idx` | `ocr_json`, `confirmed_json` |
| `decisions` | `run_id`, `idx` | `decision_json` (with candidates and Jev's answer) |
| `cart` | `run_id`, `idx` | `result_json` |

Write-only in M1, apart from a `shopping-minion runs` listing if it costs nothing.

### 3.9 CLI (`run`)

`shopping-minion run lista.yaml`:

1. load the items;
2. `ensure_logged_in`;
3. search pass, printing progress (`[3/12] atum: 12 resultados`);
4. decide pass;
5. resolve `ask` and `no_match` in the terminal;
6. print the final table and ask "adicionar ao carrinho? [s/N]";
7. cart pass, printing progress;
8. print the report, with the cart page open.

Every stage is saved in SQLite as it finishes.

## 4. Evals

- **`intake_eval.py`:**
  - Runs the OCR on the `list-001` photo, kept locally at `inbox/`, and compares the result to the fixture.
  - It reports the items found and missed, the lines split wrongly, and the constraints and quantities that are right.
  - Matching is by `name`, accent- and case-insensitive. The fixture's `brand`/`variant` are compared to `brand`/`constraints`.
  - It runs on Johann's Claude subscription, so it has no API cost.
- **`decide_eval.py`:**
  - Runs the 7 resolver cases through decide.
  - It reports, per case, the choice, the confidence and the policy status, plus the totals: correct after the policy, raw picks correct, wrong product added.
  - It runs with `batch_size` 5 and 1.
  - The fixtures use alfa0's candidate format (`id`, `unit_of_sale.kind`, `step_size_g`, `in_stock`), and the eval converts it to `Candidate`.
- The thresholds are set from these numbers, in a commit that says which run they came from.

## 5. Tasks

**T0 is mine, with Johann watching.** The others are briefed to Sonnet agents in worktrees. Johann reviews each wave before it is merged.

| Task | What | Depends on | Who |
|---|---|---|---|
| **T0** | M0: a headed Playwright session on the site, from a script in `data/tmp/` (not committed). It looks at the search response and its fields, the product id on the card, the add button and `+`, how quantities show (un/kg), the cart page, the logged-in marker, the cookie banner and checkout markers. Output: `docs/site-notes/andorinha.md`, 3 recorded search responses for test fixtures, and any §2 changes, for Johann's review. | — | Claude (Opus), live |
| **T1** | Skeleton: `pyproject.toml`, ruff and pytest config, `items.py`, `config.py` with `config/decide.yaml`, `preferences.py` with the example file, `quantity.py`, `storage.py`, with tests. | — | Sonnet |
| **T2** | intake: `intake.py` with the schema, the `ocr` command, `intake_eval.py`. | T1 | Sonnet |
| **T3** | decide: `decide.py`, `decide_eval.py`, tests with a fake client. The agent doesn't call Jev; I run the eval after the merge. | T1 | Sonnet |
| **T4** | browser, search and cart: `browser.py`, `search.py`, `cart.py`, the `login` command, offline tests on T0's fixtures, and `live` tests. | T0, T1 | Sonnet |
| **T5** | `run` command, wiring the passes and storage, plus the M1 acceptance: 3 items in the real cart. | T2, T3, T4 | Sonnet builds, Claude runs the acceptance with Johann |

- **Waves:**
  1. T0 and T1 in parallel.
  2. T2, T3 and T4.
  3. T5.
- **Paid runs, one at a time and announced before they start:** `decide_eval` (Jev, a few thousand input tokens per run, at US$0.042 per million) and the M1 acceptance run. `intake_eval` uses the Claude subscription.

### Brief template for task agents

Each brief gives:
- the task row;
- the LLD sections it implements;
- the files it owns;
- the files it must not touch;
- the done criteria: `uv run ruff check . && uv run pytest` passing, and the tests named in the task.

Every brief repeats these rules:
- no HTTP client and no hand-built requests to the store;
- no model in the browser;
- no checkout;
- don't read or print `.env.keys` or secret values;
- don't commit `data/`, `.auth/` or `inbox/`;
- report what wasn't observed as such;
- stop and report a blocker instead of working around it.

## 6. Questions resolved in review

| #   | Question       | Answer (Johann, 2026-10-01)                                             |
| --- | -------------- | ----------------------------------------------------------------------- |
| 1   | M1 review step | An edited `lista.yaml`. No review screen until M2.                      |
| 2   | Jev model      | Pinned to `jev-1.13`, moved up on purpose. Superseded by §8.1: the API only accepts `jev-latest`. |
| 3   | M1 items       | atum, papel higiênico, filé de peito de frango (with a quantity in kg). |
| 4   | Account        | Johann's real account, logged in. He clears the cart afterwards.        |

## 7. M0 changes (approved by Johann on 2026-10-01)

From [site-notes/andorinha.md](site-notes/andorinha.md). Where these differ from the sections above, **this section wins**.

1. **`Candidate` (§2) gets `slug`, and the field sources are now known.**
   - `product_id` ← `id`
   - `brand` ← `brandName`
   - `price` ← `pricing.promotionalPrice`
   - `list_price` ← `pricing.price` when `pricing.promotion` is true, else null
   - `unit_of_sale` ← `saleUnit` (`UN` → `un`, `KG` → `kg`)
   - `step_kg` ← `quantity.fraction` when KG
   - `available` ← `quantity.inStock > 0`

   Proposal: add `slug: str`. Nothing else changes.
2. **add_cart reaches the product through its page, not the search card (§3.7).**
   - Search cards don't carry the product id in the DOM.
   - The site publishes `/produtos/<id>/<slug>` for every result (in the search page's ld+json), and that page has "Adicionar ao carrinho" and the stepper.

   Proposal: cart opens `/produtos/<id>/<slug>`, clicks "Adicionar ao carrinho" once, then `+` `clicks − 1` times. It reads the stepper text after each click (e.g. "2", "200g") and waits for it to change. This is navigating to a link the site itself gives, like opening a bookmarked product. It is not building a request.
3. **"Already in the cart" check (§3.7).** Proposal: if the product page shows the stepper instead of "Adicionar ao carrinho", the product is already in the cart. The item is marked failed and left alone.
4. **Weight products with the Peso/Unidade switch.** Proposal: M1 makes sure `Peso` is checked before adding. If it isn't, the item fails rather than being switched. What "Unidade" means wasn't observed.
5. **Reading the cart (§3.7).** The cart is a drawer opened from the header button, not a page. Proposal: at the end, open the drawer and read each line's name and stepper text for the report, and leave the drawer open.
6. **Checkout guard.** The "never" test greps for `Finalizar pedido` and `checkout`.
7. **Search reads both pages of 12 (§3.3)** to reach ~15 candidates: wait for `from=0`, then `from=12` if `hasNext`, with a 10 s cap. Then keep the first 15.

## 8. Wave 2 changes (approved by Johann on 2026-10-01, see [review-wave2.md](review-wave2.md))

1. **Jev model:** `jev-latest`, since the API only accepts `jev-latest` and `jev-preview`. `Decision.model` records the version each response reports (`jev-1.13.0` on 2026-10-01). It is saved with the decision and printed by the eval.
2. **OCR model:** `intake` takes a `model` argument (default `haiku` until 2026-10-01, now `sonnet` by Johann's decision), and the eval takes `--model`. Measured on list-001 on 2026-10-01: Haiku 26/32 in 160 s, Sonnet 31/32 in 22 s.
3. **Preferences for testing:** `data/preferencias.yaml` holds the first search hit for each M1 item. Real preferences come later.
4. **list-001:** "Saco lixo pia e banheiro" is crossed out on the paper and now expects no items.
