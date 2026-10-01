"""SQLite storage: one table per stage, JSON from the contracts (LLD section 3.8)."""

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from shopping_minion.items import CartResult, Decision, Item

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
    kind   TEXT    NOT NULL,      -- state | pick | cart_edit | check
    data   TEXT    NOT NULL,      -- JSON
    PRIMARY KEY (run_id, seq)
);
"""


def _dump(model: BaseModel | None) -> str | None:
    return None if model is None else model.model_dump_json()


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

    def set_photo(self, run_id: int, photo: str) -> None:
        """The web app learns the file name (<run_id>.<ext>) only after the run row exists."""
        with self._conn:
            self._conn.execute("UPDATE runs SET photo = ? WHERE id = ?", (photo, run_id))

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

    def _replace(self, table: str, column: str, run_id: int, models: Sequence[BaseModel]) -> None:
        rows = [(run_id, idx, model.model_dump_json()) for idx, model in enumerate(models)]
        with self._conn:
            self._conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                f"INSERT INTO {table} (run_id, idx, {column}) VALUES (?, ?, ?)", rows
            )
