# ADR-0012: The store is used through its site, in a browser, the way a user would

- **Status:** Draft. Written by Claude on 2026-09-30 for Johann's review. Not a decision until he accepts it.
- **Date:** 2026-09-30
- **Would close:** the "raw HTTP stays open" clause of [ADR-0007](0007-browser-runtime-playwright-local-cdp-later.md) and the same opening in the HLD (§4.4, §4.7, §4.8).

## Context

There are several ways to automate a store that has no public API:

1. **Call an official API.** Supermarkets generally don't offer one.
2. **Drive a browser** (Playwright or similar) and use the site as a user does.
3. **Call the site's internal endpoints directly** (`curl`, an HTTP client, or `fetch()` with hand-built requests). Similar to (1) but fragile: it depends on cookies, tokens, bot protection and undocumented parameters.
4. Less common: WebSockets (same problems as 3 for auth) and WebMCP (depends on sites adopting it).

This project chose (2) from the start: [ADR-0005](0005-model-decides-executor-acts.md), [ADR-0006](0006-discovery-agent-writes-site-profile.md) and [ADR-0007](0007-browser-runtime-playwright-local-cdp-later.md) all assume a browser. But ADR-0007 and the HLD left (3) open "if discovery finds internal JSON APIs".

On 2026-09-29 and 30 the first store adapter was built through that opening. The search was described as an HTTP call and executed with an HTTP client, then with `fetch()` from a page. Every problem that followed came from (3), not from the store:

| Problem | Why it only exists when calling endpoints |
|---|---|
| Cloudflare `403` and a CORS error | The request didn't come from the page |
| A list of extra allowed hosts for the discovery agent | The spec had to name the host the API lives on |
| Store and tenant ids hardcoded in the profile | Copied from a URL the site builds by itself |
| A placeholder catalog returned for `size` ≥ 24 | A parameter value the site never sends |
| An agent reverse-engineering network traffic | It had to find "the right call" instead of using the site |

That code and its profile were removed. The account of what happened is in the [journal](../journal/2026-09-30-0007-goal-run-and-review.md).

## Options considered

1. **Keep (3) available as an optimization** when a site's internal API is convenient. Rejected: it is where all the fragility came from, and "when convenient" is exactly the judgment that went wrong.
2. **Drive the browser only.** The adapter and the discovery agent do what a user does: open pages, type in the search box, click, and read what the page shows.

## Decision (proposed)

Option 2.

- The store is reached only by driving its site in a browser. The adapter never builds requests to the site's endpoints, with an HTTP client or with `fetch()`.
- A site profile ([ADR-0006](0006-discovery-agent-writes-site-profile.md)) describes **user steps**: how to search, how to read a result, how to log in, how to add to the cart. It doesn't describe endpoints to call, hosts, ids or request parameters.
- **Reading results** (Johann, 2026-09-30), in order of preference:
  1. listen to the responses the page itself receives (observing, never sending);
  2. fall back to the DOM;
  3. where a React-like app keeps its data in a global store and not in the DOM, inject JavaScript to read that store.
- **Browser configuration** (Johann, 2026-09-30): whether the browser has a window is irrelevant. The server only sees the HTTP bytes it receives, so what matters is what the browser sends. The browser runs headless with a regular desktop user agent.

## What was tested

On 2026-09-30, the store's search page (`/busca/atum`) was opened as a user would, under six configurations. The page makes its own search call; nothing was requested by hand.

| Configuration | User agent sent | The page's search call | Items shown |
|---|---|---|---|
| A. Headless shell (Playwright's default) | `HeadlessChrome/153` | failed | 0 |
| **B. Headless shell + desktop user agent** | `Chrome/153` | **200** | **32** |
| C. Playwright's Chromium, no window | `HeadlessChrome/153` | failed | 0 |
| D. System Chromium, no window | `HeadlessChrome/152` | failed | 0 |
| E. System Chromium, with a window | `Chrome/152` | 200 | 32 |
| F. Playwright's Chromium, with a window | `Chrome/153` | 200 | 32 |

Every configuration that sends `HeadlessChrome` in the `User-Agent` header fails, whatever the binary. Every one that sends `Chrome` works, with or without a window. B is the same binary as A with only the user agent changed. In B the `sec-ch-ua` header still said "HeadlessChrome", so on this site the `User-Agent` header alone decides.

This was one site, one query, one run per configuration.

## Consequences

- No host lists, ids or request parameters in profiles, and no bot-protection workarounds for hand-built requests.
- Searching is slower (seconds per item instead of a fraction of a second). Reading the page's own responses first makes it depend less on the page's layout.
- The browser provider needs a user agent setting ([ADR-0007](0007-browser-runtime-playwright-local-cdp-later.md) doesn't have one). No window and no display are needed, which also suits a hosted setup.
- The adapter, the discovery agent and the Andorinha profile have to be built again under this rule. Contracts, intake, resolver, workflow, report and evals are unaffected.
