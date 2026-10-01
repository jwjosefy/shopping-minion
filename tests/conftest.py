import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture
def page():
    """A headless page for offline tests. Pages served to it are made up by the tests."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(locale="pt-BR")
        yield context.new_page()
        browser.close()
