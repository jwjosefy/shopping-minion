import asyncio
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from shopping_minion.browser import CDP_URL_ENV, LocalChromium, RemoteCDP, browser_provider
from shopping_minion.cli import ANDORINHA_URL, main


def _chromium_installed() -> bool:
    with sync_playwright() as pw:
        return Path(pw.chromium.executable_path).exists()


needs_chromium = pytest.mark.skipif(
    not _chromium_installed(), reason="run `uv run playwright install chromium` first"
)


def test_provider_defaults_to_local(monkeypatch):
    monkeypatch.delenv(CDP_URL_ENV, raising=False)
    assert isinstance(browser_provider(), LocalChromium)


def test_provider_uses_remote_when_cdp_url_set(monkeypatch):
    monkeypatch.setenv(CDP_URL_ENV, "ws://localhost:3000")
    provider = browser_provider()
    assert isinstance(provider, RemoteCDP)
    assert provider.endpoint == "ws://localhost:3000"


@needs_chromium
def test_local_chromium_renders_a_page():
    async def title() -> str:
        async with LocalChromium().session() as context:
            page = await context.new_page()
            await page.set_content("<title>minion</title><p>ok</p>")
            return await page.title()

    assert asyncio.run(title()) == "minion"


@pytest.mark.live
@needs_chromium
def test_browser_check_opens_andorinha(monkeypatch):
    monkeypatch.delenv(CDP_URL_ENV, raising=False)
    assert main(["browser-check", ANDORINHA_URL]) == 0
