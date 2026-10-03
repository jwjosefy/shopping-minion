import json
import sqlite3
from decimal import Decimal

import pytest

from shopping_minion import cli
from shopping_minion.items import Candidate, CartResult, Decision, Item, Quantity
from shopping_minion.report import (
    build_report,
    format_duration,
    format_report,
    list_corrections,
    report,
)
from shopping_minion.storage import Storage


def it(name, **kw):
    return Item(source_line=name, name=name, search_term=name, **kw)


def decision(name, status):
    cand = Candidate(
        product_id="p",
        slug="s",
        name="P",
        brand=None,
        price=Decimal("1.00"),
        list_price=None,
        unit_of_sale="un",
        step_kg=None,
        available=True,
    )
    return Decision(item=it(name), candidates=[cand], choice="p", confidence=0.9, status=status)


def put_log(path, run_id, rows):
    """rows: (seconds since 10:00:00, kind, data); written with exact timestamps."""
    con = sqlite3.connect(path)
    for seq, (sec, kind, data) in enumerate(rows, start=1):
        at = f"2026-10-02T{10 + sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}.000+00:00"
        con.execute(
            "INSERT INTO run_log (run_id, seq, at, kind, data) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, at, kind, json.dumps(data)),
        )
    con.commit()
    con.close()


def state(sec, name):
    return (sec, "state", {"state": name})


def pick(index, jev_choice, chosen):
    data = {"index": index, "item": "x", "jev_choice": jev_choice, "jev_confidence": 0.6}
    return (900, "pick", {**data, "chosen": chosen})


def test_format_duration():
    assert format_duration(24) == "24 s"
    assert format_duration(190) == "3 min 10 s"
    assert format_duration(365) == "6 min 05 s"
    assert format_duration(3900) == "1 h 05 min"
    assert format_duration(0.4) == "0 s"


def test_list_corrections_cases():
    a, b, c, d = it("a"), it("b"), it("c"), it("d")
    assert list_corrections([a, b], [a, b]) == (0, 0, 0)  # unchanged
    assert list_corrections([a, b], [a, it("b", brand="Camil")]) == (1, 0, 0)  # edited
    assert list_corrections([a, b, c], [a, c]) == (0, 1, 0)  # deleted
    assert list_corrections([a, c], [a, b, c]) == (0, 0, 1)  # added
    qty = it("c", quantity=Quantity(value=1, unit="kg"))
    assert list_corrections([a, c], [a, qty]) == (1, 0, 0)  # the quantity counts
    # mixed: [b, c] became [b2] (1 edited, 1 deleted), and e was added at the end
    assert list_corrections([a, b, c, d], [a, it("b2"), d, it("e")]) == (1, 1, 1)
    # a replace block where the confirmed side is longer: 1 edited, 1 added
    assert list_corrections([a, b, d], [a, it("b2"), it("b3"), d]) == (1, 0, 1)


def test_the_report_of_a_run_with_a_log(tmp_path, capsys):
    path = tmp_path / "db.sqlite"
    s = Storage(path)
    run_id = s.new_run("a.jpg")
    s.save_items(
        run_id,
        [it("arroz"), it("feijão"), it("sal"), it("leite")],
        [it("arroz"), it("feijão preto"), it("leite"), it("ovo")],
    )
    s.save_decisions(
        run_id,
        [decision("a", "accepted"), decision("b", "user_chosen"), decision("c", "skipped")],
    )
    s.save_cart(
        run_id,
        [
            CartResult(product_id="p", status="added", quantity_shown="1", message=None),
            CartResult(product_id="q", status="failed", quantity_shown=None, message="x"),
        ],
    )
    s.close()
    put_log(
        path,
        run_id,
        [
            state(0, "reading_list"),
            state(24, "reviewing_list"),
            state(24 + 190, "syncing_history"),
            state(214 + 12, "searching"),
            state(226 + 148, "deciding"),
            state(374 + 3, "picking"),
            state(377 + 365, "reviewing_cart"),
            state(742 + 80, "filling_cart"),
            state(822 + 270, "done"),
            pick(1, "1", "1"),
            pick(2, "1", "2"),
            pick(3, None, None),
            pick(4, "1", "1"),
            (
                900,
                "cart_edit",
                {"line_id": "1", "quantity": {"value": 2, "unit": "un"}, "remove": False},
            ),
            (900, "cart_edit", {"line_id": "2", "quantity": None, "remove": True}),
            (900, "check", {"ok_count": 1, "total": 2, "extras": 1}),
        ],
    )
    assert report(path, None) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"Rodada {run_id} — ")
    assert out[0].endswith(" — 4 itens na lista, 1 no carrinho")
    assert out[1:] == [
        "",
        "Tempo                                    máquina        você",
        "  lendo a lista (OCR)                       24 s",
        "  revisando a lista                               3 min 10 s",
        "  lendo os pedidos                          12 s",
        "  buscando                            2 min 28 s",
        "  decidindo (Jev)                            3 s",
        "  escolhendo produtos                             6 min 05 s",
        "  revisando o carrinho                            1 min 20 s",
        "  adicionando ao carrinho             4 min 30 s",
        "  total                               7 min 37 s 10 min 35 s   = 18 min 12 s",
        "  referência (estimativa do Johann): mais de 1 h à mão",
        "",
        "Correções",
        "  lista: 1 linha editada, 1 apagada, 1 adicionada (de 4 lidas pelo OCR)",
        "  produtos: 1 aceito pelo Jev sozinho; dos 4 que vieram para você:",
        "            2 você confirmou a escolha do Jev, 1 escolheu outro, 1 pulou",
        "  carrinho: 1 quantidade mudada, 1 removido",
        "",
        "Conferência: 1 de 2 itens conferem; 1 produto no carrinho não é desta lista",
    ]


def test_a_run_without_a_log(tmp_path, capsys):
    path = tmp_path / "db.sqlite"
    s = Storage(path)
    run_id = s.new_run("a.jpg")
    s.save_items(run_id, [it("a"), it("b")], [it("a")])
    s.close()
    assert report(path, run_id) == 0
    out = capsys.readouterr().out
    assert "sem registro de tempo" in out
    assert "lista: 0 linhas editadas, 1 apagada, 0 adicionadas (de 2 lidas pelo OCR)" in out
    assert "produtos" not in out
    assert "Conferência: sem dados" in out


def test_unknown_run_and_empty_db(tmp_path, capsys):
    path = tmp_path / "db.sqlite"
    assert report(path) == 1
    Storage(path).new_run(None)
    assert report(path, 9) == 1
    capsys.readouterr()
    assert report(path) == 0
    assert capsys.readouterr().out.startswith("Rodada 1 — ")


def test_cli_report_subcommand(tmp_path, capsys):
    path = tmp_path / "db.sqlite"
    Storage(path).new_run(None)
    cli.main(["report", "--db", str(path)])
    assert "Rodada 1" in capsys.readouterr().out


def test_build_report_is_pure_data_and_the_text_prints_it(tmp_path):
    path = tmp_path / "db.sqlite"
    s = Storage(path)
    run_id = s.new_run("a.jpg")
    s.save_items(run_id, [it("arroz"), it("sal")], [it("arroz")])
    s.save_decisions(run_id, [decision("a", "accepted"), decision("b", "user_chosen")])
    s.save_cart(
        run_id, [CartResult(product_id="p", status="added", quantity_shown="1", message=None)]
    )
    s.close()
    put_log(
        path,
        run_id,
        [
            state(0, "reading_list"),
            state(30, "picking"),
            state(90, "reviewing_cart"),
            state(100, "picking"),  # "trocar": the same item again
            state(130, "reviewing_cart"),
            state(150, "filling_cart"),
            state(160, "done"),
            pick(1, "1", "2"),
            pick(1, "1", "1"),  # reopened: the item counts once, as it ended
            pick(2, None, None),
            (900, "pick_quantity", {"index": 1, "quantity": {"value": 2, "unit": "un"}}),
            (900, "check", {"ok_count": 1, "total": 1, "extras": 0}),
        ],
    )
    storage = Storage(path)
    try:
        data = build_report(storage, run_id)
        json.dumps(data)  # plain data
        assert data["run_id"] == run_id
        assert data["items"] == 1 and data["in_cart"] == 1 and data["cart_size"] == 1
        steps = {step["state"]: step for step in data["time"]["steps"]}
        assert steps["reading_list"]["seconds"] == 30 and steps["reading_list"]["who"] == "machine"
        assert steps["picking"]["seconds"] == 60 + 30  # both visits are summed
        assert steps["picking"]["who"] == "you" and steps["picking"]["text"] == "1 min 30 s"
        assert data["time"]["machine"]["seconds"] == 30 + 10
        assert data["time"]["you"]["seconds"] == 90 + 30
        assert data["time"]["total"]["text"] == "2 min 40 s"
        assert data["corrections"]["list"] == {"edited": 0, "deleted": 1, "added": 0, "ocr": 2}
        assert data["corrections"]["products"] == {
            "accepted": 1,
            "asked": 2,
            "same": 1,
            "other": 0,
            "skipped": 1,
        }
        assert data["corrections"]["cart"] == {"changed": 0, "removed": 0}
        assert data["check"] == {"ok_count": 1, "total": 1, "extras": 0}
        text = format_report(data)
        assert text[0].endswith(" — 1 itens na lista, 1 no carrinho")
        assert text[-1] == "Conferência: 1 de 1 itens conferem"
        with pytest.raises(LookupError):
            build_report(storage, 99)
    finally:
        storage.close()
