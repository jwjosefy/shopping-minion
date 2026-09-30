# ADR-0007: Browser runtime is a Playwright-managed Chromium locally, a remote CDP browser later

- **Status:** Accepted. The "raw HTTP stays open" clause was closed by [ADR-0012](0012-the-store-is-used-through-its-site-in-a-browser.md): the store's endpoints are never called directly.
- **Date:** 2026-09-29

## Context

The store has no API, so the adapter drives a browser. The old prototype used Playwright with the system Chrome, and that led to "works on my machine" problems.

v0 has to run on anyone's machine. Later, the system should be able to run as a hosted agent (DigitalOcean, GCP, AWS, or an agent platform), not on a laptop forever.

## Options considered

1. **browserless SaaS.** No local browser at all. But the store session (cookies, credentials) goes through a third party, and it needs an account before there's anything to run.
2. **browserless Docker image locally.** Reproducible. But Docker becomes a prerequisite for v0, and Johann wants to avoid that where possible.
3. **Raw HTTP (`curl_cffi`) against the site's internal endpoints.** No browser at all. It only works if the site has usable internal APIs, which isn't known yet.
4. **Playwright with its own pinned Chromium (`playwright install chromium`), behind an interface that can also connect to a remote browser over CDP.**

## Decision

Option 4.

- v0 uses the Chromium build that Playwright downloads and pins. The system Chrome is never used. This removes most environment differences without Docker.
- The browser sits behind a `BrowserProvider` interface with two implementations: launch locally, or `connect_over_cdp` to a remote endpoint. The remote endpoint could be browserless in Docker, browserless SaaS, or the hosting platform's browser.
- Raw HTTP (option 3) stays open. If discovery ([ADR-0006](0006-discovery-agent-writes-site-profile.md)) finds internal JSON APIs, the site profile can describe HTTP calls instead of browser steps.

## Consequences

- v0 setup is `uv sync && uv run playwright install chromium`, with no Docker.
- Moving to a hosted setup changes configuration, not code.
- Options 1 and 2 remain available as values for the remote endpoint, not as rewrites.
- Risk: sites that fingerprint headless browsers may block the pinned Chromium. Discovery will surface this early. A remote browser service is the fallback.
