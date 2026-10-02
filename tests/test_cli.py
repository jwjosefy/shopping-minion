from shopping_minion.cli import main


def test_main_prints_help(capsys):
    main([])
    assert "shopping-minion" in capsys.readouterr().out


def _fake_browser(monkeypatch, logged_in: bool):
    """Replaces the browser pieces `history sync` uses; nothing is opened."""
    from contextlib import contextmanager

    from shopping_minion import browser

    class Context:
        def new_page(self):
            return "the-page"

    @contextmanager
    def fake_open_browser():
        yield None, Context()

    def fake_ensure_logged_in(page):
        assert page == "the-page"
        if not logged_in:
            raise browser.NotLoggedInError("Not logged in")

    monkeypatch.setattr(browser, "open_browser", fake_open_browser)
    monkeypatch.setattr(browser, "ensure_logged_in", fake_ensure_logged_in)


def test_history_sync_prints_the_counts(monkeypatch, tmp_path, capsys):
    from shopping_minion import orders

    _fake_browser(monkeypatch, logged_in=True)
    calls = []

    def fake_sync(page, storage, first_n, progress=None):
        calls.append((page, first_n))
        return orders.SyncResult(new=2, skipped=1, stored=12)

    monkeypatch.setattr(orders, "sync_orders", fake_sync)
    main(["history", "sync", "--db", str(tmp_path / "t.sqlite")])
    assert calls == [("the-page", 10)]
    assert "2 pedidos novos, 1 ignorados, 12 guardados" in capsys.readouterr().out


def test_history_sync_says_when_it_read_only_the_newest_ten(monkeypatch, tmp_path, capsys):
    from shopping_minion import orders

    _fake_browser(monkeypatch, logged_in=True)
    monkeypatch.setattr(
        orders,
        "sync_orders",
        lambda *a, **k: orders.SyncResult(new=10, skipped=0, stored=10, capped=True),
    )
    main(["history", "sync", "--db", str(tmp_path / "t.sqlite")])
    assert "10 mais recentes" in capsys.readouterr().out


def test_history_sync_without_login_exits_1(monkeypatch, tmp_path, capsys):
    import pytest

    from shopping_minion import orders

    _fake_browser(monkeypatch, logged_in=False)
    monkeypatch.setattr(orders, "sync_orders", lambda *a, **k: pytest.fail("must not sync"))
    with pytest.raises(SystemExit) as stop:
        main(["history", "sync", "--db", str(tmp_path / "t.sqlite")])
    assert stop.value.code == 1
    assert "shopping-minion login" in capsys.readouterr().err
