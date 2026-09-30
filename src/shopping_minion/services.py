"""Wiring for a shopping run: catalog, resolver backend, executor, preferences.

The browser session must outlive the run, so services are an async context manager. The
executor is a dry run until the cart executor exists (M4): it records what *would* be added and
touches nothing on the store.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from playwright.async_api import BrowserContext

from shopping_minion.browser import browser_provider
from shopping_minion.catalog.adapter import fetch, parse_results, rank
from shopping_minion.catalog.profile import SiteProfile, load_profile
from shopping_minion.config import load_models_config
from shopping_minion.contracts import Candidate, CartLine, SaleQuantity
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import DecisionBackend, build_backend
from shopping_minion.workflow import CartExecutor, Catalog

DEFAULT_STORE = "andorinha"


class ProfileCatalog:
    """Search through a site profile with the generic adapter (ADR-0004, ADR-0006)."""

    def __init__(self, profile: SiteProfile, context: BrowserContext) -> None:
        if profile.search is None:
            raise ValueError(f"profile {profile.store!r} has no search section; run discovery")
        self._search, self._context = profile.search, context

    async def search(self, query: str) -> list[Candidate]:
        payload = await fetch(self._context, self._search, query)
        return rank(query, parse_results(self._search, payload))


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
async def default_services(store: str = DEFAULT_STORE) -> AsyncIterator[Services]:
    config = load_models_config()
    profile = load_profile(store)
    role = config.resolver
    if role.backend != "haiku":
        raise NotImplementedError(f"resolver backend {role.backend!r} arrives in M6")
    async with browser_provider().session() as context:
        yield Services(
            catalog=ProfileCatalog(profile, context),
            backend=build_backend(role.chat_role(), f"{role.backend}:{role.model}"),
            executor=DryRunExecutor(),
            preferences=Preferences.load(),
            dry_run=True,
        )
