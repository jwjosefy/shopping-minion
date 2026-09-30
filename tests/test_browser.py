import asyncio
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from shopping_minion.browser import (
    CDP_URL_ENV,
    BrowserSettings,
    LocalChromium,
    RemoteCDP,
    browser_provider,
    load_browser_settings,
)
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


def test_settings_file_is_valid():
    settings = load_browser_settings()
    assert settings.headless is True
    assert settings.user_agent == "auto"


def test_explicit_headless_overrides_config(monkeypatch):
    monkeypatch.delenv(CDP_URL_ENV, raising=False)
    provider = browser_provider(headless=False)
    assert isinstance(provider, LocalChromium)
    assert provider.headless is False


async def _user_agent(provider: LocalChromium) -> str:
    async with provider.session() as context:
        page = await context.new_page()
        return await page.evaluate("navigator.userAgent")


@needs_chromium
def test_auto_user_agent_is_not_headless():
    ua = asyncio.run(_user_agent(LocalChromium(headless=True, user_agent="auto")))
    assert "Chrome/" in ua
    assert "HeadlessChrome" not in ua


@needs_chromium
def test_explicit_user_agent_is_reported():
    ua = asyncio.run(_user_agent(LocalChromium(user_agent="Mozilla/5.0 (test) minion/1")))
    assert ua == "Mozilla/5.0 (test) minion/1"


@needs_chromium
def test_session_is_kept_between_runs(tmp_path):
    provider = LocalChromium(auth_dir=tmp_path / "auth")
    cookie = {"name": "sid", "value": "abc", "url": "https://example.com"}

    async def add() -> None:
        async with provider.session("teststore") as context:
            await context.add_cookies([cookie])

    async def read() -> list[str]:
        async with provider.session("teststore") as context:
            return [c["value"] for c in await context.cookies("https://example.com")]

    asyncio.run(add())
    assert (tmp_path / "auth" / "teststore.json").exists()
    assert asyncio.run(read()) == ["abc"]


@pytest.mark.parametrize("name", ["../x", "a/b", "Upper", "", "-a", "a_b", "a.json"])
def test_invalid_store_name_is_rejected(tmp_path, name):
    async def go() -> None:
        async with LocalChromium(auth_dir=tmp_path).session(name):
            pass

    with pytest.raises(ValueError, match="invalid store name"):
        asyncio.run(go())
    assert list(tmp_path.iterdir()) == []


def test_settings_reject_unknown_keys():
    with pytest.raises(ValueError):
        BrowserSettings.model_validate({"headles": True})


@pytest.mark.live
@needs_chromium
def test_search_page_shows_results_in_headless():
    async def page_text() -> str:
        async with LocalChromium(headless=True).session() as context:
            page = await context.new_page()
            await page.goto("https://andorinhaonline.com.br/busca/atum")
            await page.wait_for_selector("text=/Encontramos [1-9]\\d* ite/", timeout=15_000)
            return await page.inner_text("body")

    assert re.search(r"Encontramos [1-9]\d* ite", asyncio.run(page_text()))


@pytest.mark.live
@needs_chromium
def test_browser_check_opens_andorinha(monkeypatch):
    monkeypatch.delenv(CDP_URL_ENV, raising=False)
    assert main(["browser-check", ANDORINHA_URL]) == 0
