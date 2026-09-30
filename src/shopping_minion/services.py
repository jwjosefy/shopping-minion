"""Wiring for a shopping run: catalog, resolver backend, executor, preferences.

The catalog (searching the store by driving its site in the browser) and the cart executor are
not built yet, so `default_services` refuses to start a run. Tests and previews inject their own
`Services`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from shopping_minion.contracts import Candidate, CartLine, SaleQuantity
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import DecisionBackend
from shopping_minion.workflow import CartExecutor, Catalog


class DryRunExecutor:
    """Records what would be added; changes nothing on the store."""

    async def add_to_cart(self, candidate: Candidate, sale: SaleQuantity) -> CartLine:
        return CartLine(product_id=candidate.id, quantity=sale.steps_or_units, verified=False)


@dataclass
class Services:
    catalog: Catalog
    backend: DecisionBackend
    executor: CartExecutor
    preferences: Preferences
    dry_run: bool


class ServicesFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[Services]: ...


@asynccontextmanager
async def default_services() -> AsyncIterator[Services]:
    raise NotImplementedError(
        "searching the store isn't built yet: the catalog has to drive the store's site in the "
        "browser (see docs/goal-run/)"
    )
    yield  # pragma: no cover
