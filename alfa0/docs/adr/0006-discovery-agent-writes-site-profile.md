# ADR-0006: A discovery agent writes a site profile; a generic adapter executes it

- **Status:** Accepted
- **Date:** 2026-09-29
- **Extends:** [ADR-0004](0004-store-catalog-adapters.md). **Scopes:** [ADR-0005](0005-model-decides-executor-acts.md)'s "the model never browses" to shopping time.

## Context

[ADR-0004](0004-store-catalog-adapters.md) puts everything store-specific behind a `CatalogAdapter`. It doesn't say how an adapter gets written. The obvious path is hand-written code per store. For the first store, the old prototype would have been the starting point.

Two things push against that:

- The adapter is the most fragile component by design. When the site changes, someone has to work out what changed and rewrite code.
- The project wants to test a second store later. Hand-writing every adapter means that knowledge about how supermarket sites work never accumulates anywhere.

The first store, Andorinha, has no API. Search works without login; the cart requires it.

## Options considered

1. **Hand-written adapter per store.** Predictable, but every new store and every site change is manual work. For Andorinha it would also mean reusing the old prototype, which this repo deliberately avoids (a fresh start, documented in the open).
2. **Browser agent at shopping time.** Rejected in ADR-0005.
3. **Discovery agent writes a report; a human writes the adapter from it.** Better than (1), but the output is prose, so nothing in it can be executed or tested.
4. **Discovery agent writes a structured site profile; one generic, deterministic adapter executes any profile.** Store knowledge becomes data that can be reviewed, versioned and tested.
5. **Discovery agent generates adapter code.** Flexible, but produces code per store that nobody wrote and that has to be reviewed as code.

## Decision

Option 4.

- An LLM agent with browser tools explores the store's site and writes `profiles/<store>/profile.yaml`. It records how to search, how a result maps to a `Candidate`, how the unit of sale is expressed, and how login, the cart and order history work.
- It also records fixtures of the pages and responses it relied on. These become the adapter's regression tests.
- **Timing:** it runs **once per store, before any shopping session**, and again only when a shopping run detects that the site changed. In v0, that second run is started by hand.
- **Guardrails:**
  - Checkout is not a tool the agent has.
  - The only cart write allowed is adding one test item and removing it again.
  - Credentials are injected by a login tool and never placed in the prompt.
  - A human reviews the profile, then a validation pass runs it against the live site.
- Profiles describe public site structure only and are committed, after a check for personal data. Recordings of logged-in pages stay in `data/`.
- Discovery is built together with v0. The first Andorinha profile comes from it, not from the old prototype.

## Consequences

- ADR-0005 holds at shopping time: no model drives the browser while a cart is being filled. The discovery agent is the one place where a model browses, and it runs separately and under supervision.
- A second store needs a new profile, not new code, as long as the generic adapter covers how that store's site works.
- Whether the discovery agent works can be measured: a profile either carries out search → add → read cart → remove, or it doesn't.
- Risk: the generic adapter's profile format has to cover sites that differ a lot (HTML scraping vs. internal JSON APIs). The format will grow with the second store. Keep it minimal until then.
