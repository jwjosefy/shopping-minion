"""Catalog that searches the store by driving its site in a browser (ADR-0012, LLD 2.4).

Does what a user does: run the profile's search steps in a page, then read what the page shows.
It never sends a request of its own to the store.
"""

from __future__ import annotations

from playwright.async_api import BrowserContext, Page

from shopping_minion.catalog.page import ResponseLog, read_candidates, run_steps
from shopping_minion.catalog.profile import SiteProfile
from shopping_minion.catalog.ranking import rank
from shopping_minion.contracts import Candidate


class BrowserCatalog:
    def __init__(self, profile: SiteProfile, context: BrowserContext) -> None:
        if profile.search is None:
            raise ValueError(f"site profile {profile.store!r} has no 'search' section")
        self._profile = profile
        self._context = context
        self._page: Page | None = None
        self._log = ResponseLog()

    async def _get_page(self) -> Page:
        if self._page is None:
            self._page = await self._context.new_page()
            self._log.attach(self._page)
        return self._page

    async def search(self, query: str) -> list[Candidate]:
        search = self._profile.search
        assert search is not None  # checked in __init__
        page = await self._get_page()
        self._log.clear()
        values = {"base_url": self._profile.base_url, "query": query}
        await run_steps(page, search.steps, values)
        candidates = await read_candidates(
            page, search.results, base_url=self._profile.base_url, log=self._log
        )
        return rank(query, candidates)

    async def close(self) -> None:
        if self._page is not None:
            page, self._page = self._page, None
            await page.close()
