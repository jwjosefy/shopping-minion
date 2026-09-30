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
- A site profile ([ADR-0006](0006-discovery-agent-writes-site-profile.md)) describes **user steps**: how to search, how to read a result, how to log in, how to add to the cart. It doesn't describe endpoints, hosts, ids or request parameters.
- How the browser is launched (with or without a window, which Chromium build, user agent) is configuration and Johann's choice. This ADR doesn't restrict it.

## Open questions for Johann

1. **Reading results:** from the DOM only, or may the adapter also read the responses the page itself received (observing, never sending)? The second is more robust to layout changes but reads data the user doesn't see.
2. **Browser configuration:** Playwright's default headless mode uses a separate "headless shell" binary. With it, Andorinha's search page showed no results; with the full Chromium and a visible window it worked. Only those two were tested. Not tested: the full Chromium without a window, the system Chrome (which ADR-0007 excludes and the earlier prototype used), or a changed user agent.

## Consequences

- No host lists, ids or request parameters in profiles, and no bot-protection workarounds for hand-built requests.
- Searching is slower (seconds per item instead of a fraction of a second) and depends on the page's structure.
- The adapter, the discovery agent and the Andorinha profile have to be built again under this rule. Contracts, intake, resolver, workflow, report and evals are unaffected.
