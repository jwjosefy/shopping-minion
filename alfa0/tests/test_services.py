import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from shopping_minion.catalog.browser_catalog import BrowserCatalog
from shopping_minion.catalog.profile import SiteProfile, save_profile
from shopping_minion.executor.browser_cart import BrowserCartExecutor
from shopping_minion.services import DryRunExecutor, ProfileNotDiscoveredError, default_services

FIELDS = {"id": "id", "name": "name", "url": "/p/{id}"}
EXTRACT = {"id": {"selector": ".card", "attr": "data-id"}, "name": {"selector": "a"}}
SEARCH = {
    "steps": [{"open": "{base_url}busca/{query}"}],
    "results": {
        "wait_for": ".card",
        "from_dom": {"item": ".card", "extract": EXTRACT, "fields": FIELDS},
    },
}
CART = {
    "product_card": ".card",
    "add": [{"click": ".add"}],
    "quantity": {"kind": "stepper", "click": ".plus"},
    "read": {
        "steps": [{"open": "{base_url}cart"}],
        "from_dom": {"item": ".line", "extract": EXTRACT, "fields": FIELDS},
    },
    "remove": [{"click": ".rm"}],
    "checkout_markers": ["text=Checkout"],
}


def _profile(tmp_path: Path, **extra) -> Path:
    # a made-up store that doesn't exist; only to exercise the wiring
    save_profile(
        SiteProfile.model_validate(
            {"store": "teststore", "version": 1, "base_url": "https://example.test/", **extra}
        ),
        tmp_path,
    )
    return tmp_path


class StubContext:
    async def new_page(self):
        raise AssertionError("no page should be opened by the wiring")


class FakeProvider:
    def __init__(self):
        self.stores, self.context, self.closed = [], StubContext(), False

    @asynccontextmanager
    async def session(self, store=None):
        self.stores.append(store)
        try:
            yield self.context
        finally:
            self.closed = True


def _run(store, tmp_path, **kwargs):
    provider = FakeProvider()

    async def go():
        async with default_services(
            store,
            provider=provider,
            profiles_dir=tmp_path,
            backend_factory=lambda: object(),
            **kwargs,
        ) as services:
            return services

    return asyncio.run(go()), provider


def test_missing_profile_says_not_discovered_and_names_the_command(tmp_path):
    provider = FakeProvider()

    async def go():
        async with default_services("nowhere", provider=provider, profiles_dir=tmp_path):
            pass

    with pytest.raises(ProfileNotDiscoveredError) as error:
        asyncio.run(go())
    assert "hasn't been discovered yet" in str(error.value)
    assert "shopping-minion discover nowhere --url <home page>" in str(error.value)
    assert provider.stores == []  # no browser was started


def test_profile_without_cart_runs_dry_but_not_for_real(tmp_path):
    _profile(tmp_path, search=SEARCH)
    services, provider = _run("teststore", tmp_path, dry_run=True)
    assert isinstance(services.catalog, BrowserCatalog)
    assert isinstance(services.executor, DryRunExecutor) and services.dry_run
    assert provider.stores == ["teststore"] and provider.closed

    with pytest.raises(ProfileNotDiscoveredError, match="no 'cart' section"):
        _run("teststore", tmp_path)


def test_real_run_uses_the_browser_cart_executor(tmp_path):
    _profile(tmp_path, search=SEARCH, cart=CART)
    services, provider = _run("teststore", tmp_path)
    assert isinstance(services.executor, BrowserCartExecutor) and not services.dry_run
    assert provider.closed
