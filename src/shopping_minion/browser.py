"""Playwright launch, user agent, session in .auth/ (LLD section 3.2).

The store is used only through its pages, as a user would. Nothing here talks to it
any other way.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

BASE_URL = "https://andorinhaonline.com.br"
AUTH_FILE = Path(".auth/andorinha.json")
VIEWPORT = {"width": 1366, "height": 900}
LOCALE = "pt-BR"

# Header elements, seen on the home page (M0 and T4): the profile button reads
# "Entre | Cadastre-se" when logged out.
PROFILE_BUTTON = '[data-test="profile-btn"]'
CART_BUTTON = '[data-test="cart-btn"] button'
LOGGED_OUT_TEXT = re.compile(r"Entre\s*\|\s*Cadastre-se")

# Pause after every action on the site (navigation or click) before the next one. Johann,
# 2026-10-01: 500 ms after a live run moved on too fast on a product page, then 300 ms once
# add_cart waited for the page's own UpdateCart confirmation (cart.py), which is what
# actually fixed the lost item.
ACTION_PAUSE_MS = 300


class NotLoggedInError(RuntimeError):
    """The session has no logged-in account."""


@contextmanager
def open_browser(auth_file: Path = AUTH_FILE) -> Iterator[tuple[Browser, BrowserContext]]:
    """Headed Chromium with a desktop Chrome user agent and the saved session, if any."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            # The browser's own user agent, from a blank page, minus the "Headless" marker
            # (a HeadlessChrome agent gets 0 results from the store).
            probe = browser.new_page()
            user_agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe.close()
            context = browser.new_context(
                user_agent=user_agent,
                viewport=VIEWPORT,
                locale=LOCALE,
                storage_state=str(auth_file) if auth_file.exists() else None,
            )
            yield browser, context
        finally:
            browser.close()


def save_session(context: BrowserContext, auth_file: Path = AUTH_FILE) -> Path:
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(auth_file))
    return auth_file


def settle(page: Page) -> None:
    """Pause after an action so the page can catch up (ACTION_PAUSE_MS)."""
    page.wait_for_timeout(ACTION_PAUSE_MS)


def dismiss_cookie_banner(page: Page) -> bool:
    """Click "Recusar" if the cookie banner is showing. Returns whether it clicked."""
    refuse = page.get_by_role("button", name="Recusar", exact=True)
    if refuse.count() and refuse.first.is_visible():
        refuse.first.click()
        settle(page)
        return True
    return False


def is_logged_in(page: Page, timeout_ms: int = 10_000) -> bool | None:
    """True or False once the header has rendered; None if it never did.

    The logged-out marker was observed (the header says "Entre | Cadastre-se"). The
    logged-in marker was NOT observed, so "logged in" here is an inference: the header
    rendered (the profile button has text) and the logged-out marker is absent.
    """
    profile = page.locator(PROFILE_BUTTON)
    try:
        profile.first.wait_for(state="visible", timeout=timeout_ms)
        page.wait_for_function(
            "(sel) => document.querySelector(sel)?.textContent.trim().length > 0",
            arg=PROFILE_BUTTON,
            timeout=timeout_ms,
        )
    except PlaywrightTimeout:
        return None
    return LOGGED_OUT_TEXT.search(profile.first.inner_text()) is None


def ensure_logged_in(page: Page) -> None:
    """Open the home page and raise NotLoggedInError unless the session is logged in."""
    page.goto(BASE_URL)
    settle(page)
    if is_logged_in(page) is not True:
        raise NotLoggedInError("Not logged in: run `shopping-minion login`.")
