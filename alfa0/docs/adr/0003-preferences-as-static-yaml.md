# ADR-0003: Preferences start as a hand-written YAML file

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

The resolver needs preferences to choose between search results: which brand, which size, how much is usually bought. The long-term source is purchase history, but extracting it depends on the store adapter ([ADR-0004](0004-store-catalog-adapters.md)), which is not built yet. Blocking the resolver on history extraction would push the first end-to-end run back by days.

## Decision

v0 reads preferences from a static YAML file in `data/preferences.yaml` (git-ignored; an anonymized example lives in `examples/`). One entry per canonical item, for example:

```yaml
coffee_filter:
  aliases: [filtro, filtro melita, filtro de café]
  brand: Melitta
  size: "103"
  default_quantity: 1

ground_beef:
  aliases: [carne moída]
  preferred_cut: patinho
  default_quantity: { value: 1, unit: kg }
```

The schema is the contract. Later, a generator will produce the same file from purchase history, and the resolver will not need to change.

## Consequences

- The resolver can be built and evaluated today.
- Preferences are explicit, reviewable, and editable by hand, which also makes resolver decisions easier to explain.
- The file will go stale; that is the problem the history-based generator solves later.
- Risk: the hand-written schema may not fit what history can actually provide. Keep it small until the generator exists.
