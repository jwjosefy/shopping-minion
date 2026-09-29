# 2026-09-29 — A design review with an agent

Today the project got its [high-level design](../hld.md) and five new ADRs (0006–0010). The decisions are in those documents. This entry is about how they were made, because the process surprised me more than the design did.

## Interview first, write later

I had an idea for the architecture and told Claude to interview me before writing anything. Three rounds of questions came back, each built on the previous answers and on the ADRs already in the repo, so it didn't ask about things that were already decided.

Some of the questions pushed back, and those were the useful ones:

- **"What would the Opus orchestrator decide that a fixed flow doesn't?"** My honest answer was "clarifying ambiguous items". That's v1. So v0 runs on a deterministic LangGraph graph with no LLM orchestrator ([ADR-0009](../adr/0009-langgraph-workflow-model-per-role.md)).
- **"Is Docker really the fix for 'works on my machine'?"** The problem in my old prototype was the system Chrome, not the lack of containers. Playwright's own pinned Chromium, behind an interface that can later point to a remote browser, solves it without Docker ([ADR-0007](../adr/0007-browser-runtime-playwright-local-cdp-later.md)).
- **"The discovery agent browses. ADR-0005 says the model never browses."** Answering that is what turned "an agent that figures out the site" into "an agent that writes a site profile once, before shopping, under supervision" ([ADR-0006](../adr/0006-discovery-agent-writes-site-profile.md)).

It also caught me contradicting myself. In round 1 I asked for a human review step after upload; in round 3 I said "no validation". It asked which one I meant instead of picking. I meant both: keep the list review, drop the review of the model's choices. Low-confidence items now go into the cart with a risk flag ([ADR-0008](../adr/0008-low-confidence-decisions-added-and-flagged.md)).

## I caught one back

The first HLD draft had the resolver asking two questions in two calls: which product, then how much. I pushed for a single call: the decision model should see all the product information in one context, and it halves the latency. Doing that forced a real design change. The quantity question can't be product-specific ("2 trays") if the product hasn't been chosen yet, so it asks for a *target amount* ("~1 kg") and the executor converts it ([ADR-0010](../adr/0010-single-pass-typed-resolver.md)).

What I wrote in a message right after:

> I just caught an error in its HLD and asked it to fix it. In a normal pre-AI company setting, that would take a full one-hour meeting with engineers stepping on each other just to write down what needs to change, and then scheduling another review round…

This reminded me of my years as a TPM at Amazon and the endless design reviews. The review itself took minutes. The part that used to take a week, from comments to a revised doc to the next review, collapsed into one conversation.

## The Obsidian loop

The flow that ended up working, which I didn't plan:

1. Claude writes into a folder.
2. I open Obsidian on that same folder.
3. Claude generates the HLD.
4. I review, edit and add comments **directly in the HLD**, the way an engineer would on a shared doc. I answered the open questions inline with `>` under each one.
5. Back in Claude, I ask it to take the inline answers into account.
6. It folds **all** the changes into the document, moves the answered questions into a "Resolved questions" table, and aligns the dependent sections (contracts, confidence policy, ADR list, plan).
7. I do a final pass and approve.

The document is the interface. There's no separate thread of comments to reconcile and no "see my notes" email. The review lives in the artifact, and the history lives in git.

## What I'd keep doing

- **Ask to be interviewed before anything is written.** The questions were worth more than the first draft would have been.
- **Review in the document itself.** Inline answers are unambiguous, and the agent treats them as instructions about the doc, not as chat.
- **Let the agent point to the ADRs a change contradicts.** Three of the five new ADRs exist because it flagged a conflict with ADR-0005 instead of quietly working around it.
