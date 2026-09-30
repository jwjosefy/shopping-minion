"""Wiring for a shopping run: catalog, resolver backend, executor, preferences (LLD 2.8).

`default_services` opens one browser session for the whole run, loads the store's site profile and
builds the browser catalog and cart executor on it (or a dry-run executor that adds nothing).
Tests and previews inject their own `Services`, or the provider, profiles directory and backend
factory of `default_services`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from shopping_minion.browser import BrowserProvider, browser_provider
from shopping_minion.catalog.browser_catalog import BrowserCatalog
from shopping_minion.catalog.profile import DEFAULT_PROFILES_DIR, load_profile
from shopping_minion.config import load_models_config
from shopping_minion.contracts import Candidate, CartLine, SaleQuantity
from shopping_minion.executor.browser_cart import BrowserCartExecutor
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import DecisionBackend, build_resolver_backend
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


class ProfileNotDiscoveredError(RuntimeError):
    """The store has no site profile yet, or its profile can't support this run."""


def _default_backend() -> DecisionBackend:
    return build_resolver_backend(load_models_config().resolver)


@asynccontextmanager
async def default_services(
    store: str = "andorinha",
    *,
    dry_run: bool = False,
    provider: BrowserProvider | None = None,
    profiles_dir: Path = DEFAULT_PROFILES_DIR,
    backend_factory: Callable[[], DecisionBackend] = _default_backend,
) -> AsyncIterator[Services]:
    try:
        profile = load_profile(store, profiles_dir)
    except FileNotFoundError as e:
        raise ProfileNotDiscoveredError(
            f"store {store!r} hasn't been discovered yet (no site profile in {profiles_dir}). "
            f"Run: shopping-minion discover {store} --url <home page>"
        ) from e
    if profile.cart is None and not dry_run:
        raise ProfileNotDiscoveredError(
            f"the site profile for {store!r} has no 'cart' section, so it can't add to the cart. "
            f"Its cart hasn't been discovered yet (discovery session 2); until then, use --dry-run"
        )

    provider = provider or browser_provider()
    async with provider.session(store=store) as context:
        catalog = BrowserCatalog(profile, context)
        executor: BrowserCartExecutor | DryRunExecutor = (
            DryRunExecutor() if dry_run else BrowserCartExecutor(profile, context)
        )
        try:
            yield Services(
                catalog=catalog,
                backend=backend_factory(),
                executor=executor,
                preferences=Preferences.load(),
                dry_run=dry_run,
            )
        finally:
            await catalog.close()
            if isinstance(executor, BrowserCartExecutor):
                await executor.close()
