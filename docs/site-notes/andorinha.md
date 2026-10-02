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

## Cart sync (logged in), seen on 2026-10-01

- With a logged-in session, each "Adicionar ao carrinho" or `+` makes the page send its own GraphQL mutation `operationName: "UpdateCart"`, about 0.6 s after the click. A 200 came back about 0.1 s later.
- **The stepper and the drawer update before that.** A run that moved on to the next product too soon reported papel higiênico as added (stepper "1"), and the real cart, reloaded, didn't have it.
- An anonymous cart sends no cart request at all. **Inference:** it lives only in the browser.
- So: an item counts as added only after the page's own UpdateCart gets a 200. The final report reloads the page before reading the drawer.

## Session

- Anonymous: the header shows **"Entre | Cadastre-se"**. That is the logged-out marker.
- **Not observed yet:** the logged-in marker, and what a returning session's cart shows. That needs Johann to log in, in T4's `login` command.
- A cookie banner ("Usamos cookies…") with **Recusar** / **Aceitar tudo** / **Escolher** shows on the first page of a fresh context. Clicking "Recusar" dismissed it.

## Quantities above 1 kg, seen on 2026-10-02

The stepper and the cart drawer write weights below 1 kg as grams (`900g`) and above 1 kg with a **dot** (`1.1kg`, `1.5kg`). In run 10, every kg product whose target passed 1 kg was reported as failed at the click that crossed 1 kg ("a quantidade não mudou"), and its drawer line was left out of the check. The cause was the stepper pattern accepting only a comma. The clicks themselves had gone through.

## Several Peso/Unidade switches on a produce page, seen on 2026-10-02

A produce product page (Banana Prata Kg) has 4 switches named "Seletor de unidade de venda": one in the buy box, and three on the suggested products' cards. In run 11, five kg products failed with a Playwright strict-mode error because the code looked for the switch on the whole page. The switch is now read inside the buy box.

## Order history (M4 T12), seen on 2026-10-02

Looked at with Johann's logged-in session, headed. The script only navigated, and clicked only "Ver mais produtos". No personal data is written here: no order numbers, addresses, dates or totals. The values in the examples are made up.

**The order list, `/minha-conta/pedidos`**
- The page receives its own GraphQL response, `operationName: "CustomerOrdersListPaginated"`, from `api.andorinhaonline.com.br/storefront/graphql`. It has 10 orders per page, newest first, with `pageInfo.hasNextPage` and an `endCursor`.
- Each row has:
  - `id` (the number in `/minha-conta/pedidos/<id>`);
  - `createdAt` (UTC, ISO 8601), `deliveryDate`;
  - `status` (`FINISHED` on every order seen);
  - `total`;
  - `items[]` with `productId` and `name`, **without quantity**.
- The links to each order are plain `<a href="/minha-conta/pedidos/<id>">` on the page.

**One order, `/minha-conta/pedidos/<id>`**
- The page receives `operationName: "OrderDetailsQuery"`. **Its `items[]` already has every product.** "Ver mais produtos" only expands the list on the page: clicking it sent no request. So reading an order doesn't need the click.
- Each item has:
  - `productId` and `slug`;
  - `name`, `category`;
  - `quantity` (a number), `saleUnit` (`UN` or `KG`);
  - `selectedSaleUnit`, `sellByWeightAndUnit`;
  - `totalPrice`;
  - `productType` (`PRODUCT` or `VARIABLE`; weighed products were `VARIABLE`).
- **`productId` is the same id the search uses** (the search hit's `id`, our `Candidate.product_id`). Checked against run 8: of the 316 distinct products its search returned, 62 appear by id in the last 10 orders, and so do 14 of the 28 distinct products Johann ended up with.
- **Weighed products show what was weighed, not what was ordered.** For example, `quantity: 3.68` for a product ordered as 4 kg. What was ordered appears only as text, in `changedItemsHistory[]` with `type: "CHANGED"`, like "O item X teve sua quantidade alterada de 4kg. para 3.68kg."
- The same response also has the delivery address, the payment method and the status history. None of it is needed.
- **Older orders, seen on 2026-10-02 in the first sync:** `selectedSaleUnit` and `sellByWeightAndUnit` are `null` on some lines, with `saleUnit` set and a valid `quantity`. The sync falls back to `saleUnit`, which was lowercase `un` on 2 lines, so the unit is read in any case. A product that wasn't delivered shows `quantity: 0` and `totalPrice: 0`, and is left out.

**Buttons on the order page.** Besides "Ver mais produtos" / "Ver menos produtos", the page shows:
- "Avaliar pedido" and "Avaliar";
- "Mais detalhes";
- **"Adicionar todos os itens ao carrinho"**, which writes to the cart. Code must never click it, and a guard test forbids that text in `src/`, like "Finalizar pedido".

**Also seen on the home page:** `PublicLastBoughtProductsQuery`, a list of recently bought products with full product data (pricing, stock, `fraction`). M4 doesn't need it; noted for later.

## What this changes in the LLD

See LLD §7 (M0 changes, for review).
