# 2026-09-29 — Origin and scoping

## Where this comes from

Our grocery list lives on a piece of paper on the fridge. Whoever notices something is missing writes it down. Turning that paper into an online order meant retyping every line, searching each item, picking the right product out of dozens of results, and figuring out quantities that the site expresses in its own units.

A while ago my son and I built a first prototype: photo → an LLM for OCR → a Python script that searched the store for each item, compared results against my past orders, and filled the cart. It worked often enough to prove the idea, and was brittle enough that I never trusted it without checking every line.

## What I am doing differently

The prototype treated this as a script with an LLM call in the middle. I am treating it as a system design problem instead, with each concern separated:

- **Intake:** photo to structured items.
- **Preferences:** what I usually buy, derived from order history.
- **Catalog:** deterministic search and normalization. The model never browses.
- **Resolver:** the model picks a candidate and a quantity, and explains why.
- **Executor:** deterministic validation, then the cart change. Checkout stays manual.

The key decision is recorded in [ADR-0005](../adr/0005-model-decides-executor-acts.md).

## Scope for v0

A working path end-to-end, even if narrow, beats polished components that don't connect. v0 is done when one real photo of a real list produces a cart I would only have to glance at.

Explicitly out of scope for now: multiple stores, multi-user, substitutions when an item is out of stock, and a UI.

## Build in the open

This repo includes the reasoning, not only the code: ADRs for decisions, and this journal for what I tried, what I rejected, and why. Some of these entries will later become blog posts.
