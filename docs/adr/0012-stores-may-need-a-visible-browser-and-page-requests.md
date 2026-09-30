# ADR-0012: A store profile may require a visible browser and requests made from a page

- **Status:** Proposed. Written during an unattended run on 2026-09-30; needs Johann's review.
- **Date:** 2026-09-30
- **Extends:** [ADR-0007](0007-browser-runtime-playwright-local-cdp-later.md) (the risk it named happened) and [ADR-0006](0006-discovery-agent-writes-site-profile.md) (the discovery guardrails).

## Context

Andorinha's product search is a `GET` to `sense.osuper.com.br`, a host that Andorinha's own pages call (the relationship between the two isn't documented anywhere we found; the store and tenant ids in the URL, 269 and 1327, match the ids in Andorinha's own GraphQL responses). It sits behind Cloudflare bot protection:

- a plain HTTP client, including Playwright's request context, gets `403`;
- `fetch()` from a store page in **headless** Chromium fails with a CORS error (the `403` carries no CORS headers);
- `fetch()` from a store page in a **visible** Chromium returns `200` with the full catalog JSON.

So the runtime needs to be able to (a) show a browser window and (b) send the request from inside a page of the store. ADR-0006 also limited discovery to the store's own domain, which excludes the host that serves the search.

## Options considered

1. **Change the browser identity** (user agent, `navigator.webdriver`) so headless Chromium passes. Rejected: it's evading a site's bot protection, and it would break whenever the protection changes.
2. **Use the remote CDP browser** (ADR-0007's fallback). Works, but means running a separate browser service for a v0 that works locally.
3. **Let the profile say how to reach the API:** `headed: true` and `transport: page`, and let a human list extra API hosts for discovery.

## Decision

Option 3.

- `SiteProfile.headed` runs the pinned Chromium with a visible window. Nothing about the browser's identity is changed.
- `HttpSearch.transport: page` (with `page_url`) sends the request with `fetch()` from a store page kept open for the run.
- `--allow-domain` on `discover` lets a human add an API host that search specs may call. The agent still can't open pages or click outside the store's domain, and profiles still can't carry cookies or credential headers.
- `page_size` is part of the profile, and a response with more items than requested is an error (the site's API misbehaves silently above a size).

## Consequences

- Running the shopping flow on this store needs a display. A hosted setup ([ADR-0007](0007-browser-runtime-playwright-local-cdp-later.md)) would need a remote browser with a window (headed) or a service the store's protection accepts; that hasn't been tested.
- Cloudflare may change its rules. When it does, the adapter's `ProfileError` says so, and rediscovery is the response.
- Whether automating this store fits its terms of use isn't something the code can answer. Johann should check them before real use.
