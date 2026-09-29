# ADR-0004: Store access goes through a catalog adapter

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

Everything store-specific (search, product pages, how units of sale are expressed, the cart, order history) changes from one supermarket to another and can change without notice on the same one. If those details leak into the resolver or the executor, the core logic becomes coupled to one website's HTML.

## Decision

The core talks to stores only through an adapter interface. The first adapter targets one supermarket in São Paulo.

```python
class CatalogAdapter(Protocol):
    def search(self, query: str) -> list[Candidate]: ...
    def add_to_cart(self, product_id: str, quantity: SaleQuantity) -> CartLine: ...
    def cart(self) -> list[CartLine]: ...
    def order_history(self) -> list[PastOrderLine]: ...   # post-v0, feeds ADR-0003's generator
```

Every `Candidate` carries a normalized **unit of sale**, because this is where most quantity errors come from:

| Kind | Example | Quantity means |
|---|---|---|
| `unit` | 1 can of tuna | number of items |
| `pack` | toilet paper, 12 rolls | number of packs |
| `weight_step` | ham, +100 g per click | number of steps; the adapter knows the step size |

## Consequences

- The resolver reasons about normalized candidates, never about HTML.
- Converting "600 g of ham" into three clicks of a 200 g stepper happens in one place (the adapter plus executor validation), where it can be unit-tested.
- A second store means writing a second adapter, without changing the core.
- The adapter is the most fragile component by design; it gets its own recorded fixtures so site changes show up as test failures, not as wrong carts.
