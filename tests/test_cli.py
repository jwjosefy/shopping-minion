from shopping_minion.cli import main


def test_main_prints_help(capsys):
    main([])
    assert "shopping-minion" in capsys.readouterr().out
