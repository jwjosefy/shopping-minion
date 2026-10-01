import json
import sqlite3
from decimal import Decimal

from shopping_minion.items import Candidate, CartResult, Decision, Item, Quantity
from shopping_minion.storage import Storage


def make_item(name: str) -> Item:
    return Item(
        source_line=name, name=name, search_term=name, quantity=Quantity(value=1, unit="kg")
    )


def make_decision(item: Item) -> Decision:
    candidate = Candidate(
        product_id="p1",
        slug="atum",
        name="Atum",
        brand=None,
        price=Decimal("9.90"),
        list_price=None,
        unit_of_sale="un",
        step_kg=None,
        available=True,
    )
    return Decision(
        item=item,
        candidates=[candidate],
        choice="p1",
        confidence=0.9,
        probabilities={"p1": 0.9},
        status="accepted",
    )


def rows(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_creates_parent_dir_and_tables(tmp_path):
    path = tmp_path / "nested" / "dir" / "db.sqlite"
    Storage(path).close()
    names = {r[0] for r in rows(path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "items", "decisions", "cart"} <= names


def test_reopening_existing_db_is_fine(tmp_path):
    path = tmp_path / "db.sqlite"
    first = Storage(path)
    run_id = first.new_run("a.jpg")
    first.close()
    second = Storage(path)
    assert [r["id"] for r in second.list_runs()] == [run_id]


def test_new_run_and_status(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    first = storage.new_run("a.jpg")
    second = storage.new_run(None)
    assert second == first + 1
    storage.set_status(first, "done")
    runs = storage.list_runs()
    assert [(r["id"], r["photo"], r["status"]) for r in runs] == [
        (first, "a.jpg", "done"),
        (second, None, "new"),
    ]
    assert runs[0]["created_at"]


def test_save_items_stores_json_per_index(tmp_path):
    path = tmp_path / "db.sqlite"
    storage = Storage(path)
    run_id = storage.new_run("a.jpg")
    ocr = [make_item("atum"), make_item("arroz")]
    confirmed = [make_item("atum"), make_item("arroz"), make_item("sal")]
    storage.save_items(run_id, ocr, confirmed)
    got = rows(path, "SELECT idx, ocr_json, confirmed_json FROM items ORDER BY idx")
    assert [g[0] for g in got] == [0, 1, 2]
    assert Item.model_validate_json(got[0][1]) == ocr[0]
    assert Item.model_validate_json(got[2][2]) == confirmed[2]
    assert got[2][1] is None  # no OCR counterpart for the added item


def test_save_is_idempotent_per_run(tmp_path):
    path = tmp_path / "db.sqlite"
    storage = Storage(path)
    run_id = storage.new_run("a.jpg")
    storage.save_items(run_id, [make_item("a"), make_item("b")], [make_item("a")])
    storage.save_items(run_id, [make_item("a")], [make_item("a")])
    assert rows(path, "SELECT COUNT(*) FROM items") == [(1,)]


def test_save_decisions_roundtrip(tmp_path):
    path = tmp_path / "db.sqlite"
    storage = Storage(path)
    run_id = storage.new_run("a.jpg")
    decisions = [make_decision(make_item("atum")), make_decision(make_item("sal"))]
    storage.save_decisions(run_id, decisions)
    got = rows(path, "SELECT decision_json FROM decisions ORDER BY idx")
    assert [Decision.model_validate_json(g[0]) for g in got] == decisions
    assert json.loads(got[0][0])["candidates"][0]["price"] == "9.90"


def test_save_cart_roundtrip(tmp_path):
    path = tmp_path / "db.sqlite"
    storage = Storage(path)
    run_id = storage.new_run("a.jpg")
    results = [
        CartResult(product_id="p1", status="added", quantity_shown="2", message=None),
        CartResult(product_id="p2", status="failed", quantity_shown=None, message="boom"),
    ]
    storage.save_cart(run_id, results)
    got = rows(path, "SELECT result_json FROM cart ORDER BY idx")
    assert [CartResult.model_validate_json(g[0]) for g in got] == results


def test_run_log_appends_with_a_seq_per_run(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    first, second = storage.new_run("a.jpg"), storage.new_run("b.jpg")
    storage.log(first, "state", {"state": "reading_list"})
    storage.log(second, "state", {"state": "searching"})
    storage.log(first, "pick", {"item": "ação", "chosen": None})
    log = storage.read_log(first)
    assert [(r["seq"], r["kind"]) for r in log] == [(1, "state"), (2, "pick")]
    assert log[1]["data"] == {"item": "ação", "chosen": None}
    assert [r["seq"] for r in storage.read_log(second)] == [1]
    assert storage.read_log(999) == []
    at = log[0]["at"]  # UTC, with milliseconds
    assert at.endswith("+00:00") and len(at.split("T")[1].split("+")[0].split(".")[1]) == 3


def test_run_log_table_is_added_to_an_existing_db(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL, photo TEXT, status TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO runs (created_at, photo, status) VALUES ('x', NULL, 'done')")
    conn.commit()
    conn.close()
    storage = Storage(path)
    storage.log(1, "state", {"state": "done"})
    assert [r["kind"] for r in storage.read_log(1)] == ["state"]
    assert [r["id"] for r in storage.list_runs()] == [1]
