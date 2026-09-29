"""Browser runtime (ADR-0007).

v0 launches the Chromium that Playwright downloads and pins (`playwright install chromium`); the
system Chrome is never used. Setting BROWSER_CDP_URL switches to a remote browser (browserless in
Docker, browserless SaaS, or a hosting platform's browser) without code changes.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from playwright.async_api import BrowserContext, async_playwright

CDP_URL_ENV = "BROWSER_CDP_URL"


class BrowserProvider(Protocol):
    def session(self) -> AbstractAsyncContextManager[BrowserContext]:
        """A browser context that is closed when the block exits."""
        ...


@dataclass(frozen=True)
class LocalChromium:
    headless: bool = True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[BrowserContext]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context()
                yield context
            finally:
                await browser.close()


@dataclass(frozen=True)
class RemoteCDP:
    endpoint: str

    @asynccontextmanager
    async def session(self) -> AsyncIterator[BrowserContext]:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(self.endpoint)
            try:
                context = await browser.new_context()
                yield context
            finally:
                await browser.close()


def browser_provider(*, headless: bool = True) -> BrowserProvider:
    """RemoteCDP when BROWSER_CDP_URL is set, LocalChromium otherwise."""
    endpoint = os.environ.get(CDP_URL_ENV)
    if endpoint:
        return RemoteCDP(endpoint)
    return LocalChromium(headless=headless)
