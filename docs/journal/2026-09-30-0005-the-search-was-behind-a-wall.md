# 2026-09-30 — The search was behind a wall the agent couldn't see

_Draft written during an unattended run on 2026-09-30. Johann to review before publishing._

The discovery agent ([ADR-0006](../adr/0006-discovery-agent-writes-site-profile.md)) got its first real job: learn how Andorinha's search works. It failed three times, and the reasons say more about the design than a success would have.

## What went wrong, in the order it was found

1. **The free model tier ran out.** 20 requests per day per model wasn't enough for an agent that needs 30 to 60 calls. Moving the role to GLM 5.3 Flash through OpenRouter fixed that.
2. **The agent found the wrong API.** It spent its calls on `api.andorinhaonline.com.br/storefront/graphql`, which answers locations, schedules and frequent search terms, but not product searches. Its tool for reading network traffic didn't show request headers, and its log didn't show what the tools returned, so neither the agent nor I could tell why its drafts were rejected.
3. **The real search call is on another host and fails in headless Chromium.** Loading `/busca/atum` shows "Encontramos 0 itens" and an error toast. The console has the reason: the search is a plain `GET` to `sense.osuper.com.br/269/1327/search?...`, a platform vendor's API, and the browser call dies with a CORS error. A `curl` gets a Cloudflare "Attention Required" page (HTTP 403).
4. **The agent's domain guard would have refused the right answer anyway.** Specs were only allowed on `andorinhaonline.com.br`. The API is on `osuper.com.br`.

## What works

| Client | Result |
|---|---|
| `curl` | 403 (Cloudflare) |
| Playwright request context (a non-page HTTP client) | 403 |
| Headless Chromium, `fetch()` from a store page | CORS error (the 403 has no CORS headers) |
| **Visible Chromium, `fetch()` from a store page** | **200, full JSON** |

The difference is the browser's identity: headless Chromium announces itself as `HeadlessChrome`, and the protection rejects that. Nothing was spoofed to get through: the same browser with a window is a normal browser. [ADR-0007](../adr/0007-browser-runtime-playwright-local-cdp-later.md) had named this risk ("sites that fingerprint headless browsers may block the pinned Chromium"), and it showed up on the first real site.

What changed in the design ([ADR-0012](../adr/0012-stores-may-need-a-visible-browser-and-page-requests.md), proposed):

- A profile can say `headed: true` and `transport: page`: the request is made with `fetch()` from inside a page of the store, in a visible browser.
- A human can allow an extra API host for search specs (`--allow-domain osuper.com.br`). The agent still can't browse or click outside the store's domain.
- The discovery tools now record the page's non-secret request headers and log tool results.

## The trap in the API

With the search working I ran the three v0 items and got 22,408 results, all from a "store 0" catalog with fake products ("Arroz Tipo 1 5kg", id 999000001), no prices, all out of stock, and no error. The adapter asked for `size=40`. Testing sizes: up to 20 the API answers the real search; from 24 it silently returns a placeholder catalog.

Two changes came out of it:

- `page_size` is part of the profile (20 for Andorinha), not a constant in the code.
- The adapter refuses a response with more items than requested. That's a cheap check that turns "garbage in the cart" into a `ProfileError`, which the workflow reads as "the site changed, rediscovery needed".

## The profile, for now

The agent hasn't produced Andorinha's profile yet: the OpenRouter balance ran out before it could retry with the fixed tools. The committed profile is hand-written from the network probe, says so in its `notes`, and is covered by offline tests against recorded responses. How the API expresses units of sale:

- `saleUnit` is `UN` or `KG`.
- For `KG`, `quantity.fraction` is the stepper increment in kilos: 0.1 for sliced chicken breast, 0.3 for sliced ham, 0.9 for a whole piece.
- Packs only appear in the name: "C/16 Rolos", "Lv12 Pg11". The pack regex misses "Leve 16 Pague 15" and "Pacote 24un", so those show as single units.
- There is no stable product URL (cards navigate with JavaScript), so a candidate's link points to the search for its exact name.
- The store id (`269/1327`) is what the site selects by default. It's not something we chose.

## Lessons

- **Give the agent what a human would use:** request headers, tool results in the log, and the host list.
- **Fail loudly on the site's silent modes:** an API that answers 200 with a placeholder catalog is worse than one that errors.
- **Write the profile by hand once:** doing the discovery manually found four problems in the agent's tools that its own runs never surfaced.
