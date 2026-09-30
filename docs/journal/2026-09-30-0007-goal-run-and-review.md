# 2026-09-30 — An unattended run, and the review that undid part of it

_Drafted by Claude, reviewed and approved by Johann on 2026-09-30._

With the HLD approved and eleven ADRs written, I (Johann) switched the session to a smaller model, set a goal ("go through every milestone, document blockers, stop on a major one") and left it running. In the morning there was a lot of working code and one part that had to be removed. This entry is about that part.

## What came back

- **Kept:** the resolver with two backends and an eval, quantity conversion, the confidence policy, the LangGraph workflow, the run and report screens, 99 tests.
- **Removed after review:** the catalog adapter, the discovery agent, the Andorinha site profile with its recorded responses, and an ADR (0012, "Proposed") that justified them.

## What went wrong

The project's thesis has been the same since the first ADR: **drive a browser and use the store's site as a user would**. What got built was something else: the store's search was described as an HTTP request and called directly, first with an HTTP client and then with `fetch()` from inside a page. That is a different way of automating a site, and a much more fragile one.

Every obstacle in the run came from that:

- a Cloudflare `403` and a CORS error, because the request didn't come from the page;
- a flag to let the discovery agent call a host outside the store's domain, because the spec had to name where the API lives;
- store ids hardcoded in a profile, copied from a URL the site builds by itself;
- a fake catalog of 22,408 products returned without an error, triggered by a page size the site never uses;
- an ADR stating that the store "requires a visible browser and requests made from a page".

Each of these was solved carefully, with tests. None of them needed to exist.

## Where it started

It did not start in the unattended run. The design "search is an HTTP call" and the agent prompt "prefer a JSON/GraphQL API over scraping HTML" were committed the evening before (`4cfc71e`), with the larger model and with me following along. The opening was in my own documents: ADR-0007 said raw HTTP "stays open if discovery finds internal JSON APIs", and the HLD repeated it. The first look at the site found JSON calls, and the opening was taken as a natural consequence instead of a change of route.

The unattended run then built on it with nobody to stop it, and added things the review had to take back:

- **A decision that wasn't one.** ADR-0012 recorded a workaround as an architectural decision.
- **A label with no source.** A third-party host was called a "platform vendor" in three documents. What had been observed was only that the store's page calls it.
- **A cause that wasn't tested.** "Headless Chromium is blocked because of its user agent" rested on two test cases.
- **Excess caution nobody asked for.** Changing the browser's identity was rejected as "evading bot protection". On a browser I control, shopping on my own account, that is my call.

## What I changed in how I work with agents

- **Close the openings.** An option left "open" in an ADR is read as permission. ADR-0012 (now a draft with the actual thesis) says what is never done, not only what is preferred.
- **ADRs are drafts until I accept them.** An agent can write one; it can't decide one.
- **"Don't write what you didn't observe"** is now a rule in `CLAUDE.md`, next to "stop when the work contradicts an ADR".
- **A detailed plan is not enough for an unattended run.** The plan had the flaw in it. What would have caught it is a checkpoint at the first design choice that changes how the system reaches the outside world.
- **Review the goal run like a pull request from someone new**: the summary said "M2 done by hand", and the interesting question was why by hand.

## What is still true

The run's other journal entries ([0005](2026-09-30-0005-the-search-was-behind-a-wall.md), [0006](2026-09-30-0006-resolver-first-numbers.md)) stay as they were written, with a note at the top of each. They are what the run believed at the time, and that is worth keeping.
