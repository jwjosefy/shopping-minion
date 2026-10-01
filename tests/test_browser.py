import pytest

from shopping_minion.browser import (
    NotLoggedInError,
    dismiss_cookie_banner,
    ensure_logged_in,
    is_logged_in,
    save_session,
)

HEADER_OUT = '<header><div data-test="profile-btn"><span>Entre | Cadastre-se</span></div></header>'
HEADER_IN = '<header><div data-test="profile-btn"><span>Olá, Johann</span></div></header>'


def test_logged_out_header(page):
    page.set_content(HEADER_OUT)
    assert is_logged_in(page, timeout_ms=1000) is False


def test_logged_in_is_marker_absent(page):
    page.set_content(HEADER_IN)
    assert is_logged_in(page, timeout_ms=1000) is True


def test_unknown_when_header_never_renders(page):
    page.set_content("<p>loading</p>")
    assert is_logged_in(page, timeout_ms=300) is None


def test_ensure_logged_in_says_to_run_login(page):
    page.route(
        "**/*",
        lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=HEADER_OUT),
    )
    with pytest.raises(NotLoggedInError, match="shopping-minion login"):
        ensure_logged_in(page)


def test_ensure_logged_in_passes_when_logged_in(page):
    page.route(
        "**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=HEADER_IN)
    )
    ensure_logged_in(page)


def test_cookie_banner_refused_only_when_visible(page):
    page.set_content("<p>nothing</p>")
    assert dismiss_cookie_banner(page) is False
    page.set_content(
        '<div id=b><button onclick="this.parentElement.remove()">Recusar</button>'
        "<button>Aceitar tudo</button></div>"
    )
    assert dismiss_cookie_banner(page) is True
    assert page.locator("#b").count() == 0
    assert dismiss_cookie_banner(page) is False


def test_save_session_creates_the_folder(page, tmp_path):
    target = tmp_path / ".auth" / "andorinha.json"
    save_session(page.context, target)
    assert target.exists()


def test_login_subcommand_is_registered():
    from shopping_minion.cli import build_parser

    assert build_parser().parse_args(["login"]).command == "login"
