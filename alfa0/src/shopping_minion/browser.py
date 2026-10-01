"""Browser runtime (ADR-0007).

v0 launches the Chromium that Playwright downloads and pins (`playwright install chromium`); the
system Chrome is never used. Setting BROWSER_CDP_URL switches to a remote browser (browserless in
Docker, browserless SaaS, or a hosting platform's browser) without code changes.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml
from playwright.async_api import Browser, BrowserContext, async_playwright
from pydantic import BaseModel, ConfigDict

CDP_URL_ENV = "BROWSER_CDP_URL"
DEFAULT_BROWSER_CONFIG_PATH = Path("config/browser.yaml")
DEFAULT_AUTH_DIR = Path(".auth")  # ignored by version control
AUTO_USER_AGENT = "auto"
_STORE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class BrowserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headless: bool = True
    user_agent: str = AUTO_USER_AGENT


def load_browser_settings(path: Path = DEFAULT_BROWSER_CONFIG_PATH) -> BrowserSettings:
    with path.open(encoding="utf-8") as f:
        return BrowserSettings.model_validate(yaml.safe_load(f) or {})


def storage_state_path(store: str, auth_dir: Path = DEFAULT_AUTH_DIR) -> Path:
    """`<auth_dir>/<store>.json`; the store name is lowercase letters, digits and hyphens."""
    if not _STORE_NAME.fullmatch(store):
        raise ValueError(f"invalid store name {store!r}: use lowercase letters, digits and hyphens")
    return auth_dir / f"{store}.json"


async def _resolve_user_agent(browser: Browser, setting: str) -> str:
    if setting != AUTO_USER_AGENT:
        return setting
    probe = await browser.new_context()
    try:
        page = await probe.new_page()
        reported: str = await page.evaluate("navigator.userAgent")
    finally:
        await probe.close()
    return reported.replace("HeadlessChrome", "Chrome")


@asynccontextmanager
async def _session(
    browser: Browser, user_agent: str, store: str | None, auth_dir: Path
) -> AsyncIterator[BrowserContext]:
    state_file = storage_state_path(store, auth_dir) if store is not None else None
    try:
        ua = await _resolve_user_agent(browser, user_agent)
        saved = state_file if state_file is not None and state_file.exists() else None
        context = await browser.new_context(user_agent=ua, storage_state=saved)
        yield context
        if state_file is not None:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=state_file)
    finally:
        await browser.close()


class BrowserProvider(Protocol):
    def session(self, store: str | None = None) -> AbstractAsyncContextManager[BrowserContext]:
        """A browser context that is closed when the block exits.

        With `store`, the login session is loaded from `.auth/<store>.json` if it exists and
        saved back when the block exits normally.
        """
        ...


@dataclass(frozen=True)
class LocalChromium:
    headless: bool = True
    user_agent: str = AUTO_USER_AGENT
    auth_dir: Path = field(default=DEFAULT_AUTH_DIR)

    @asynccontextmanager
    async def session(self, store: str | None = None) -> AsyncIterator[BrowserContext]:
        if store is not None:
            storage_state_path(store, self.auth_dir)  # validate before launching
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            async with _session(browser, self.user_agent, store, self.auth_dir) as context:
                yield context


@dataclass(frozen=True)
class RemoteCDP:
    endpoint: str
    user_agent: str = AUTO_USER_AGENT
    auth_dir: Path = field(default=DEFAULT_AUTH_DIR)

    @asynccontextmanager
    async def session(self, store: str | None = None) -> AsyncIterator[BrowserContext]:
        if store is not None:
            storage_state_path(store, self.auth_dir)
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(self.endpoint)
            async with _session(browser, self.user_agent, store, self.auth_dir) as context:
                yield context


def browser_provider(
    *, headless: bool | None = None, settings: BrowserSettings | None = None
) -> BrowserProvider:
    """RemoteCDP when BROWSER_CDP_URL is set, LocalChromium otherwise.

    Settings come from config/browser.yaml unless given; an explicit `headless` overrides them.
    """
    settings = settings or load_browser_settings()
    endpoint = os.environ.get(CDP_URL_ENV)
    if endpoint:
        return RemoteCDP(endpoint, user_agent=settings.user_agent)
    return LocalChromium(
        headless=settings.headless if headless is None else headless,
        user_agent=settings.user_agent,
    )
