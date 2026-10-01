# Andorinha — what M0 observed

_Written by Claude on 2026-10-01 from a headed Playwright session (Playwright's Chromium, desktop Chrome user agent, 1366×900, pt-BR, anonymous, fresh context per run). Everything here was seen in that session, unless it is marked as an inference. Scripts are in `data/tmp/m0_*.py` (not committed). Screenshots are in `data/tmp/m0/`._

## Search

- `/busca/<term>` loads the results by itself. The page shows "Encontramos N itens": atum 32, papel higiênico 20, file de peito de frango 265.
- The page receives the results as JSON from `sense.osuper.com.br/269/1327/search?...`, in pages of 12 (`size=12&from=0`, then `from=12`). Both pages arrived within 8 s without scrolling. This is the page's own request; we only read its response. Match it by URL path `/search` and the `search=` parameter equal to the term.
- **The page double-encodes the term** in its own call (seen in T4): "papel higienico" goes out as `search=papel%2520higienico`, and the store still answers with the right hits. Match the `search` parameter as it is or decoded once more.
- Response: `hits[]`, `total`, `nextFrom`, `hasNext`, `hasPrevious`, `extraData`.
- Fields of a hit that matter:

| Field | Example | Use |
|---|---|---|
| `id` | `"6137677"` | product id |
| `slug` | `"atum-solido-coqueiro-natural-170g"` | product page URL |
| `name` | `"Atum Sólido Coqueiro Natural 170g"` | |
| `brandName` | `"Coqueiro"` | brand |
| `pricing.price` | `33.99` | list price |
| `pricing.promotionalPrice` | `23.99` | price paid (equal to `price` when `promotion` is false) |
| `pricing.promotion`, `pricing.discount` | `true`, `29` | discount, % |
| `saleUnit` | `"UN"` or `"KG"` | unit of sale |
| `quantity.fraction` | `1` (UN), `0.1` / `0.3` / `0.5` / `0.9` (KG) | step per click; for KG, in kg |
| `quantity.min`, `quantity.max` | `0.1`, `24` | bounds |
| `quantity.inStock` | `8943`, `408.45` | availability (> 0) |
| `quantity.sellByWeightAndUnit` | `true` for "Filé De Peito Frango Resf Kg" | the page shows a Peso/Unidade switch |
| `content`, `contentUnit` | `null` in all 36 hits seen | not usable for size |

- Packs show only in the name: "C/16 Rolos", "Leve 16 Pague 15", "Oferta 24UN".
- The search page also carries a `<script type="application/ld+json">` `ItemList` with each result's URL: `http://andorinhaonline.com.br/produtos/<id>/<slug>`.
- Recorded responses (first page of 12) for tests: `tests/fixtures/search/{atum,papel-higienico,file-de-peito-de-frango}.json`. They are public catalog data from an anonymous session.

## Search cards

- Each card is a `div.item-product-wrapper` containing an `<a>` **with no `href` and no id attribute**. Navigation is done by JavaScript. The card's text is "R$ 13,98 | un | Atum Sólido Coqueiro Natural 170g", and its only button is the round `+`.
- **The product id is not in the card's DOM.** I looked at its attributes and at the React props of the nearest component, and found no id there. Picking a card by id is not possible from the DOM. By name text it would be possible.

## Product page

- `/produtos/<id>/<slug>` opens the product page: name, SKU, price ("R$ 13,98un"), and a **"Adicionar ao carrinho"** button. It worked by direct navigation to the URL the site publishes in its ld+json.
- After "Adicionar ao carrinho", the button becomes a stepper `[trash] 1 [+]`. Each `+` raises it by one step:
  - **UN (atum):** 1 → 2 → 3.
  - **KG in Peso mode (filé de peito de frango, `fraction` 0.1):** 100g → 200g → 300g. The first add is already one step (100 g).
- The stepper is a `div` with exactly two direct `button` children, and its text is the quantity ("3", "300g"). Its classes are utility classes (`flex items-center overflow-clip rounded-full …`), not stable names.
- **The Peso/Unidade switch** (`role=radiogroup`, `aria-label="Seletor de unidade de venda"`, radios `Peso` / `Unidade`) shows for `sellByWeightAndUnit` products. Peso is checked by default.
  - One of my early runs clicked "Unidade" by mistake. The cart then showed "1" at R$ 2,80, the same price as 100 g.
  - What a "unidade" means for that product wasn't checked. **Inference:** M1 should use Peso only, and leave the switch alone.

## Cart

- The header cart button shows **the number of distinct products**, not units: atum ×3 still showed `1`.
- Clicking it opens a **drawer** ("Carrinho"), not a new page; the URL doesn't change. For each product the drawer shows the name, the line total and a stepper with the quantity ("3", "300g"), grouped by category.
- The footer has the total and **"Finalizar pedido"**, which is the checkout button. With R$ 8,40 in the cart it was disabled, with the notice "O valor mínimo do pedido deve ser R$ 30,00".
- Checkout markers for the "never" test: `Finalizar pedido`, plus `/checkout` as an inference, since that URL wasn't visited.

## Session

- Anonymous: the header shows **"Entre | Cadastre-se"**. That is the logged-out marker.
- **Not observed yet:** the logged-in marker, and what a returning session's cart shows. That needs Johann to log in, in T4's `login` command.
- A cookie banner ("Usamos cookies…") with **Recusar** / **Aceitar tudo** / **Escolher** shows on the first page of a fresh context. Clicking "Recusar" dismissed it.

## What this changes in the LLD

See LLD §7 (M0 changes, for review).
