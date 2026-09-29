import pytest

from shopping_minion.discovery.tools import BLOCKED_ACTION, DiscoverySession, registrable_domain


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://andorinhaonline.com.br/", "andorinhaonline.com.br"),
        ("https://api.andorinhaonline.com.br/storefront/graphql", "andorinhaonline.com.br"),
        ("https://www.example.com/x", "example.com"),
    ],
)
def test_registrable_domain(url, domain):
    assert registrable_domain(url) == domain


def test_session_only_allows_the_store_domain():
    session = DiscoverySession(None, None, "andorinhaonline.com.br", [])
    assert session.allowed("https://api.andorinhaonline.com.br/graphql")
    assert not session.allowed("https://evil-andorinhaonline.com.br/")
    assert not session.allowed("https://maps.googleapis.com/")


@pytest.mark.parametrize(
    "label", ["Adicionar ao carrinho", "Finalizar compra", "button.add-to-cart", "Entrar", "Login"]
)
def test_cart_and_login_actions_are_blocked(label):
    assert BLOCKED_ACTION.search(label)


@pytest.mark.parametrize("label", ["Fechar", "Buscar", "text=Açougue", "button.close-modal"])
def test_browsing_actions_are_allowed(label):
    assert not BLOCKED_ACTION.search(label)


def test_browser_errors_are_returned_to_the_model():
    import asyncio

    from playwright.async_api import Error as PlaywrightError

    from shopping_minion.discovery.tools import _errors_to_model

    @_errors_to_model
    async def flaky():
        raise PlaywrightError("Locator.click: Timeout 5000ms exceeded.\nCall log: ...")

    assert asyncio.run(flaky()) == "error: Locator.click: Timeout 5000ms exceeded."
