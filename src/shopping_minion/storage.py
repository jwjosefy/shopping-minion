"""SQLite storage: one table per stage, JSON from the contracts (LLD section 3.8)."""

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from shopping_minion.items import CartResult, Decision, Item
from shopping_minion.orders import Order, OrderLine
from shopping_minion.preferences import validate_entry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    photo TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    ocr_json TEXT,
    confirmed_json TEXT,
    PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    decision_json TEXT NOT NULL,
    PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS cart (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, idx)
);
CREATE TABLE IF NOT EXISTS run_log (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    seq    INTEGER NOT NULL,
    at     TEXT    NOT NULL,      -- UTC, ISO 8601 with milliseconds
    kind   TEXT    NOT NULL,      -- state | pick | cart_edit | check | decide | history
    data   TEXT    NOT NULL,      -- JSON
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS orders (
    order_id  TEXT PRIMARY KEY,
    placed_at TEXT NOT NULL,     -- UTC, ISO 8601
    status    TEXT NOT NULL,
    total     TEXT,              -- Decimal as text
    synced_at TEXT NOT NULL      -- UTC, ISO 8601
);
CREATE TABLE IF NOT EXISTS order_lines (
    order_id    TEXT    NOT NULL REFERENCES orders(order_id),
    line        INTEGER NOT NULL,
    product_id  TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    quantity    REAL    NOT NULL,
    unit        TEXT    NOT NULL,   -- un | kg
    total_price TEXT,
    PRIMARY KEY (order_id, line)
);
CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,    -- the item name, normalized (preferences.normalize_key)
    entry_json TEXT NOT NULL        -- the entry, with the pt-BR fields of preferencias.yaml
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utc_text(moment: datetime) -> str:
    """UTC, always with microseconds, so the text sorts the way the moments do."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _dump(model: BaseModel | None) -> str | None:
    return None if model is None else model.model_dump_json()


def photo_list(value: str | None) -> list[str]:
    """The paths in a `runs.photo` value: a JSON list, or (old rows) one plain path."""
    if not value:
        return []
    if value.startswith("["):
        try:
            paths = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else [value]
    return [value]


class Storage:
    def __init__(self, path: str | Path = "data/shopping-minion.sqlite") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def new_run(self, photo: str | None) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (created_at, photo, status) VALUES (?, ?, ?)",
                (datetime.now(UTC).isoformat(timespec="seconds"), photo, "new"),
            )
        return cur.lastrowid

    def save_items(
        self,
        run_id: int,
        ocr_items: Sequence[Item],
        confirmed_items: Sequence[Item],
    ) -> None:
        """Rows are paired by position; the shorter list is padded with NULL."""
        rows = []
        for idx in range(max(len(ocr_items), len(confirmed_items))):
            ocr = ocr_items[idx] if idx < len(ocr_items) else None
            confirmed = confirmed_items[idx] if idx < len(confirmed_items) else None
            rows.append((run_id, idx, _dump(ocr), _dump(confirmed)))
        with self._conn:
            self._conn.execute("DELETE FROM items WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                "INSERT INTO items (run_id, idx, ocr_json, confirmed_json) VALUES (?, ?, ?, ?)",
                rows,
            )

    def save_decisions(self, run_id: int, decisions: Sequence[Decision]) -> None:
        self._replace("decisions", "decision_json", run_id, decisions)

    def save_cart(self, run_id: int, results: Sequence[CartResult]) -> None:
        self._replace("cart", "result_json", run_id, results)

    def set_photo(self, run_id: int, photos: str | Sequence[str]) -> None:
        """The web app learns the file names (<run_id>-<n>.<ext>) only after the run row exists.
        `runs.photo` keeps a JSON list of paths."""
        value = json.dumps([photos] if isinstance(photos, str) else list(photos))
        with self._conn:
            self._conn.execute("UPDATE runs SET photo = ? WHERE id = ?", (value, run_id))

    def photos(self, run_id: int) -> list[str]:
        row = self._conn.execute("SELECT photo FROM runs WHERE id = ?", (run_id,)).fetchone()
        return photo_list(None if row is None else row["photo"])

    def set_status(self, run_id: int, status: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))

    def list_runs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, photo, status FROM runs ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_runs(self, limit: int = 5) -> list[dict]:
        """Newest first: {run_id, created_at, items (confirmed count), status}."""
        rows = self._conn.execute(
            "SELECT r.id AS run_id, r.created_at, r.status, "
            "(SELECT COUNT(*) FROM items i WHERE i.run_id = r.id "
            " AND i.confirmed_json IS NOT NULL) AS items "
            "FROM runs r ORDER BY r.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def log(self, run_id: int, kind: str, data: dict) -> None:
        """Append a row to the run's log; `seq` is the next one for this run."""
        at = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._conn:
            self._conn.execute(
                "INSERT INTO run_log (run_id, seq, at, kind, data) "
                "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM run_log WHERE run_id = ?",
                (run_id, at, kind, json.dumps(data, ensure_ascii=False), run_id),
            )

    def read_log(self, run_id: int) -> list[dict]:
        """{seq, at, kind, data} per row, in order."""
        rows = self._conn.execute(
            "SELECT seq, at, kind, data FROM run_log WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def read_items(self, run_id: int) -> tuple[list[Item], list[Item]]:
        """(OCR items, confirmed items), in order, without the padding NULLs."""
        rows = self._conn.execute(
            "SELECT ocr_json, confirmed_json FROM items WHERE run_id = ? ORDER BY idx", (run_id,)
        ).fetchall()
        ocr = [Item.model_validate_json(r["ocr_json"]) for r in rows if r["ocr_json"]]
        confirmed = [
            Item.model_validate_json(r["confirmed_json"]) for r in rows if r["confirmed_json"]
        ]
        return ocr, confirmed

    def read_decisions(self, run_id: int) -> list[Decision]:
        rows = self._conn.execute(
            "SELECT decision_json FROM decisions WHERE run_id = ? ORDER BY idx", (run_id,)
        ).fetchall()
        return [Decision.model_validate_json(r["decision_json"]) for r in rows]

    def read_cart(self, run_id: int) -> list[CartResult]:
        rows = self._conn.execute(
            "SELECT result_json FROM cart WHERE run_id = ? ORDER BY idx", (run_id,)
        ).fetchall()
        return [CartResult.model_validate_json(r["result_json"]) for r in rows]

    def known_order_ids(self) -> set[str]:
        return {row["order_id"] for row in self._conn.execute("SELECT order_id FROM orders")}

    def save_order(self, order: Order) -> None:
        """One transaction. An order already stored is an error (sync saves only new ones)."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO orders (order_id, placed_at, status, total, synced_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    order.order_id,
                    _utc_text(order.placed_at),
                    order.status,
                    None if order.total is None else str(order.total),
                    _utc_text(datetime.now(UTC)),
                ),
            )
            self._conn.executemany(
                "INSERT INTO order_lines "
                "(order_id, line, product_id, name, quantity, unit, total_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        order.order_id,
                        number,
                        line.product_id,
                        line.name,
                        line.quantity,
                        line.unit,
                        None if line.total_price is None else str(line.total_price),
                    )
                    for number, line in enumerate(order.lines)
                ],
            )

    def order_lines(self, before: datetime | None = None) -> list[OrderLine]:
        """Every stored line, newest order first; with `before`, only orders placed before it."""
        sql = (
            "SELECT l.order_id, o.placed_at, l.product_id, l.name, l.quantity, l.unit, "
            "l.total_price FROM order_lines l JOIN orders o ON o.order_id = l.order_id"
        )
        params: tuple = ()
        if before is not None:
            sql += " WHERE o.placed_at < ?"
            params = (_utc_text(before),)
        rows = self._conn.execute(sql + " ORDER BY o.placed_at DESC, l.order_id, l.line", params)
        return [
            OrderLine(
                order_id=row["order_id"],
                placed_at=datetime.fromisoformat(row["placed_at"]),
                product_id=row["product_id"],
                name=row["name"],
                quantity=row["quantity"],
                unit=row["unit"],
                total_price=None if row["total_price"] is None else Decimal(row["total_price"]),
            )
            for row in rows
        ]

    def read_preferences(self) -> dict[str, dict]:
        rows = self._conn.execute("SELECT key, entry_json FROM preferences ORDER BY key")
        return {row["key"]: json.loads(row["entry_json"]) for row in rows}

    def set_preference(self, key: str, entry: dict) -> None:
        """Create or replace. The entry is validated; a bad `quantidade` raises ValueError."""
        entry = validate_entry(entry)
        with self._conn:
            self._conn.execute(
                "INSERT INTO preferences (key, entry_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET entry_json = excluded.entry_json",
                (key, json.dumps(entry, ensure_ascii=False)),
            )

    def delete_preference(self, key: str) -> bool:
        """True when there was an entry to remove."""
        with self._conn:
            cur = self._conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
        return cur.rowcount > 0

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def _replace(self, table: str, column: str, run_id: int, models: Sequence[BaseModel]) -> None:
        rows = [(run_id, idx, model.model_dump_json()) for idx, model in enumerate(models)]
        with self._conn:
            self._conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                f"INSERT INTO {table} (run_id, idx, {column}) VALUES (?, ?, ?)", rows
            )
