<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/minion-dark.svg">
    <img src="docs/assets/minion.svg" alt="Minion" width="120">
  </picture>
</p>

# Shopping Minion

An agent that turns a handwritten grocery list into a ready-to-review online shopping cart.

You take a photo of the list stuck to the fridge. Shopping Minion reads it, figures out which catalog product each line actually means (based on your preferences), works out the right quantity for each product's unit of sale, and fills the cart. It stops before checkout: **a human always reviews and places the order.**

> Status: **v0 in progress.** This repo is built in the open; the reasoning behind each decision lives in [`docs/`](docs/).
>
> Today the app can't run a real list: searching the store isn't built, so the app starts a run only in tests and previews. Built so far: intake, the review web app, the resolver, quantity conversion and the workflow. Not built: the store catalog adapter, login and the cart executor, so nothing is added to a cart yet.

## Why this is harder than it looks

"Leite" on a piece of paper is not a product. Turning it into the right item in the cart means solving several problems:

- **Perception:** handwriting in, structured items out.
- **Intent resolution:** a search returns 17 kinds of milk. Which one did the person mean?
- **Preferences:** the answer usually lives in purchase history (brand, size, recurring quantity), not in the list.
- **Units of sale:** some items are sold per unit, some per pack, and some by weight through +/- steppers (e.g., +500 g per click). "1 kg of ground beef" has to become the right number of clicks.
- **Safe execution:** an LLM should never have unchecked write access to a shopping cart.

## How it works

```mermaid
flowchart TD
    photo[/"List photo"/] --> intake["Intake<br/>(OCR + parsing)"]
    intake --> review1(["Human review<br/>(web UI)"])
    review1 -- items --> catalog
    history[/"Purchase history"/] --> prefs["Preferences"]
    prefs --> catalog["Catalog<br/>(search + normalize, deterministic)"]
    catalog -- structured candidates --> resolver["Resolver<br/>(LLM, structured output)"]
    resolver -- "decision: product + target quantity" --> executor["Executor<br/>(deterministic, validates, then acts)"]
    executor --> cart["Cart"]
    cart --> review2(["Human review"])
    review2 --> checkout(["Checkout<br/>(manual)"])

    classDef human fill:#fde68a,stroke:#b45309,color:#1f2937
    classDef llm fill:#c7d2fe,stroke:#4338ca,color:#1f2937
    classDef det fill:#d1fae5,stroke:#047857,color:#1f2937
    class review1,review2,checkout human
    class intake,resolver llm
    class catalog,executor,cart det
```

<sub>🟨 human step · 🟦 LLM · 🟩 deterministic code</sub>

This is the design. The catalog, the executor and the cart are not built yet, and the purchase history step is planned for v1: v0 reads a hand-written preferences file. The model picks the product; the quantity is derived by rule (the list, then the preferences, then 1 unit flagged for review, [ADR-0013](docs/adr/0013-target-quantity-is-derived-not-asked.md)).

**Design principles**

1. **Retrieval is deterministic; reasoning is the model's job.** The catalog layer searches the store and returns clean, structured candidates. The model never browses the site.
2. **The model decides; it does not act.** The resolver emits a structured decision. The executor validates it against product rules (unit of sale, stock, quantity bounds) before touching the cart. See [ADR-0005](docs/adr/0005-model-decides-executor-acts.md).
3. **Humans at the two points where errors hide.** The transcribed list is reviewed before anything is searched ([ADR-0002](docs/adr/0002-human-review-of-transcribed-list.md)), and nothing is ever purchased automatically.
4. **Store-agnostic core.** Store-specific code lives behind a catalog adapter ([ADR-0004](docs/adr/0004-store-catalog-adapters.md)). The first adapter targets one supermarket in São Paulo.

## Getting started

Requires [uv](https://docs.astral.sh/uv/). No Docker ([ADR-0007](docs/adr/0007-browser-runtime-playwright-local-cdp-later.md)).

```bash
uv sync
uv run playwright install chromium   # Playwright's pinned Chromium, never the system Chrome
uv run shopping-minion browser-check # opens the store's home page headless
uv run pytest                        # add `-m live` for tests that hit real sites
```

Set `BROWSER_CDP_URL` to use a remote browser instead of the local one.

Start the web app, where you can upload a photo and review the transcribed list (needs `ANTHROPIC_API_KEY` in the encrypted `.env`, see [ADR-0001](docs/adr/0001-secrets-with-dotenvx.md)):

```bash
dotenvx run -- uv run shopping-minion serve        # http://127.0.0.1:8000
dotenvx run -- uv run shopping-minion serve --lan  # reachable from a phone on the same network
```

### Julia-1 backend (optional)

The resolver can run on [Julia-1](https://huggingface.co/SupersonicLabs/Julia-1), a small local decision model (Apache 2.0, runs on CPU), instead of an LLM ([ADR-0010](docs/adr/0010-single-pass-typed-resolver.md)). It isn't a project dependency because it needs PyTorch:

```bash
uv pip install huggingface_hub
uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('SupersonicLabs/Julia-1', local_dir='data/models/Julia-1')"
uv pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple "torch>=2.6"
uv pip install "transformers>=5.0,<5.1" safetensors numpy -e data/models/Julia-1
```

Run with `uv run --no-sync ...` afterwards: a plain `uv sync` removes packages that aren't in the lockfile. Compare backends on the recorded cases with `uv run --no-sync python evals/harness/resolver_eval.py --backend julia1`.

## Repository layout

```
docs/hld.md      High-level design (v0)
docs/lld.md      Low-level design: the tasks left for v0
docs/adr/        Architecture Decision Records
docs/journal/    Build log: what I tried, what I rejected, and why
src/             Source code (added as components land)
evals/           Ground-truth fixtures and the intake / resolver eval harness
data/            Local personal data: history, preferences. Never committed.
```

## Secrets and personal data

- Secrets are managed with [dotenvx](https://dotenvx.com) ([ADR-0001](docs/adr/0001-secrets-with-dotenvx.md)): `.env` is committed **encrypted**; the decryption key (`.env.keys`) is never committed.
- Browser session state, purchase history, and anything under `data/` stay local and are git-ignored.
- Evaluation fixtures are anonymized before they enter the repo.

## Roadmap

- [ ] v0: list photo → human review → structured items → one resolved product → item added to the cart
- [ ] v0: full list → full cart, stopping before checkout
- [ ] Preferences generated from purchase history (v0 uses a hand-written YAML, [ADR-0003](docs/adr/0003-preferences-as-static-yaml.md))
- [ ] Evaluation harness (product match, quantity/unit accuracy, human correction rate)
- [ ] Decision rationale shown to the user at review time

## License

MIT
