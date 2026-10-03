"""A stub of the HTTP API in LLD-M2 section 5, for test_ui.py.

Canned data and a scripted run, serving the real static files. It never touches the store.
States listed in `hold` wait at the end of that state until the test calls
POST /_stub/release/<state>, so a test can look at a working screen as long as it likes.
"""

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent.parent / "src" / "shopping_minion" / "web" / "static"

LIST_ITEMS = [
    {
        "source_line": "leite int. 1l",
        "name": "leite integral",
        "search_term": "leite integral 1l",
        "constraints": ["integral"],
        "brand": None,
        "quantity": {"value": 1, "unit": "l"},
        "needs_review": False,
    },
    {
        "source_line": "tomate ??",
        "name": "tomate",
        "search_term": "tomate italiano",
        "constraints": [],
        "brand": None,
        "quantity": {"value": 1, "unit": "kg"},
        "needs_review": True,
    },
    {
        "source_line": "requeijão",
        "name": "requeijão",
        "search_term": "requeijão cremoso",
        "constraints": ["cremoso", "sem lactose"],
        "brand": "Catupiry",
        "quantity": None,
        "needs_review": False,
    },
]


def _candidate(pid, name, price, *, brand="Marca", list_price=None, unit="un", step=None, ok=True):
    return {
        "product_id": pid,
        "slug": pid,
        "name": name,
        "brand": brand,
        "price": price,
        "list_price": list_price,
        "unit_of_sale": unit,
        "step_kg": step,
        "available": ok,
        "image": f"/stub-img/{pid}.svg",
        "description": "",
    }


PICKS = [
    {
        "item": LIST_ITEMS[1],
        "candidates": [
            _candidate("t-1", "Tomate italiano bandeja", "9.90", unit="kg", step=0.5),
            _candidate(
                "t-jev", "Tomate italiano kg", "7.50", list_price="9.00", unit="kg", step=0.5
            ),
            _candidate("t-3", "Tomate cereja 300g", "6.99", ok=False),
        ],
        "jev": {"choice": "t-jev", "confidence": 0.56, "nothing_fit": False},
        # the list says 1 kg: no flag
        "quantities": {
            pid: {"value": 1, "unit": "kg", "flags": []} for pid in ("t-1", "t-jev", "t-3")
        },
    },
    {
        "item": LIST_ITEMS[2],
        "candidates": [
            _candidate("r-1", "Requeijão cremoso 200g", "8.49"),
            _candidate("r-2", "Requeijão light 200g", "7.99"),
        ],
        "jev": {"choice": None, "confidence": None, "nothing_fit": True},
        # r-1 was bought before, 2 at a time; r-2 never: the default
        "quantities": {
            "r-1": {"value": 2, "unit": "un", "flags": ["QUANTITY_FROM_HISTORY"]},
            "r-2": {"value": 1, "unit": "un", "flags": ["QUANTITY_ASSUMED"]},
        },
    },
]

# What a search for a new term finds, for the item with no results.
SEARCH_FOUND = [
    _candidate("sg-1", "Sal grosso 1kg", "3.49"),
    _candidate("sg-2", "Sal grosso moído 1kg", "4.20", list_price="5.00"),
]

# A long list of candidates, to scroll the picker on a phone.
MANY_PICK = {
    "item": {**LIST_ITEMS[1], "name": "arroz", "search_term": "arroz agulhinha 5kg"},
    "candidates": [
        _candidate(f"a-{n}", f"Arroz agulhinha tipo {n} 5kg", f"{20 + n}.90") for n in range(12)
    ],
    "jev": {"choice": "a-0", "confidence": 0.6, "nothing_fit": False},
    "quantities": {
        f"a-{n}": {"value": 1, "unit": "un", "flags": ["QUANTITY_ASSUMED"]} for n in range(12)
    },
}

# One line read as two products, for the grouped review card.
GROUPED_ITEMS = [
    {
        "source_line": "saco lixo pia e banheiro",
        "name": f"saco de lixo ({where})",
        "search_term": f"saco de lixo {where}",
        "constraints": [where],
        "brand": None,
        "quantity": None,
        "needs_review": False,
    }
    for where in ("pia", "banheiro")
]

# Draft lines: (line_id, candidate, item names, quantity, flags)
DRAFT = [
    (
        "p-leite",
        _candidate("p-leite", "Leite integral 1L", "4.50", list_price="5.20"),
        ["leite integral"],
        {"value": 1, "unit": "un"},
        ["QUANTITY_FROM_HISTORY"],
    ),
    (
        "p-tomate",
        _candidate("p-tomate", "Tomate italiano kg", "8.00", unit="kg", step=0.5),
        ["tomate"],
        {"value": 1, "unit": "kg"},
        ["QUANTITY_INEXACT"],
    ),
    (
        "p-req",
        _candidate("p-req", "Requeijão cremoso 200g", "5.00"),
        ["requeijão", "requeijão"],
        {"value": 2, "unit": "un"},
        ["QUANTITY_ASSUMED"],
    ),
]

OUTCOME = {
    "checks": [
        {
            "item_names": ["leite integral"],
            "product_name": "Leite integral 1L",
            "expected": "1",
            "found": "1",
            "was_before": False,
            "ok": True,
            "verdict": "ok",
        },
        {
            "item_names": ["tomate"],
            "product_name": "Tomate italiano kg",
            "expected": "1kg",
            "found": None,
            "was_before": False,
            "ok": False,
            "verdict": "FALTANDO no carrinho",
        },
        {
            "item_names": ["requeijão", "requeijão"],
            "product_name": "Requeijão cremoso 200g",
            "expected": "2",
            "found": "1",
            "was_before": False,
            "ok": False,
            "verdict": "QUANTIDADE DIFERENTE: carrinho tem 1, esperado 2",
        },
    ],
    "problems": [
        {
            "line_id": "p-tomate",
            "item_names": ["tomate"],
            "product_name": "Tomate italiano kg",
            "expected": "1kg",
            "found": None,
            "message": "o site não aceitou o clique em Adicionar",
        },
        {
            "line_id": "p-req",
            "item_names": ["requeijão", "requeijão"],
            "product_name": "Requeijão cremoso 200g",
            "expected": "2",
            "found": "1",
            "message": "QUANTIDADE DIFERENTE: carrinho tem 1, esperado 2",
        },
    ],
    "extras": [{"name": "Sabão em pó 1kg", "quantity": "1", "was_before": True}],
    "ok_count": 1,
    "total": 3,
}

# What the retry leaves: the two lines again, now ok.
OUTCOME_AFTER_RETRY = {
    "checks": [
        {**OUTCOME["checks"][1], "found": "1kg", "ok": True, "verdict": "ok"},
        {**OUTCOME["checks"][2], "found": "2", "ok": True, "verdict": "ok"},
    ],
    "problems": [],
    "extras": OUTCOME["extras"],
    "ok_count": 2,
    "total": 2,
}

# An item the search found nothing for, even after the retry (picker with `no_results`).
NO_RESULTS_PICK = {
    "item": {**LIST_ITEMS[1], "name": "sal grosso", "search_term": "sal grosso"},
    "candidates": [],
    "jev": {"choice": None, "confidence": None, "nothing_fit": False},
    "no_results": True,
    "quantities": {},
}

# What the done screen shows under the outcome (GET /api/run/report).
REPORT = {
    "run_id": 1,
    "when": "2026-10-02 10:00",
    "items": 3,
    "in_cart": 1,
    "cart_size": 3,
    "time": {
        "steps": [
            {
                "state": "reading_list",
                "label": "lendo a lista (OCR)",
                "who": "machine",
                "seconds": 24,
                "text": "24 s",
            },
            {
                "state": "picking",
                "label": "escolhendo produtos",
                "who": "you",
                "seconds": 365,
                "text": "6 min 05 s",
            },
            {
                "state": "filling_cart",
                "label": "adicionando ao carrinho",
                "who": "machine",
                "seconds": 270,
                "text": "4 min 30 s",
            },
        ],
        "machine": {"seconds": 294, "text": "4 min 54 s"},
        "you": {"seconds": 365, "text": "6 min 05 s"},
        "total": {"seconds": 659, "text": "10 min 59 s"},
        "baseline": "referência (estimativa do Johann): mais de 1 h à mão",
    },
    "corrections": {
        "list": {"edited": 1, "deleted": 0, "added": 2, "ocr": 3},
        "products": {"accepted": 1, "asked": 2, "same": 1, "other": 1, "skipped": 0},
        "cart": {"changed": 1, "removed": 1},
    },
    "check": {"ok_count": 1, "total": 3, "extras": 1},
}

WORKING = {"reading_list", "syncing_history", "searching", "deciding", "filling_cart"}
WAITING = {"reviewing_list", "picking", "reviewing_cart"}


class WrongState(Exception):
    pass


class Stub:
    def __init__(self, hold=(), access=True, picks=None, items=None):
        self.initial_items = LIST_ITEMS if items is None else items
        self.hold = set(hold)
        self.picks = list(PICKS if picks is None else picks)
        self.access = access
        self.lock = threading.RLock()
        self.closing = False
        self.events_down = False  # the event stream answers 503, as a dropped connection would
        self.stream_opens: list[int] = []  # the `after` of every stream opened
        self.snapshots = 0
        self.events: list[dict] = []  # not cleared by reset(): seq only grows
        self.reset()
        self.released: set[str] = set()
        self.pending: dict = {}
        # what the tests assert on
        self.uploads: list[str] = []  # the file names of every photo sent, in order
        self.list_puts: list[dict] = []
        self.prefs: dict[str, dict] = {
            "feijao": {"nome": "feijão", "variante": "carioca", "excluir": ["preto"]}
        }
        self.picks_posted: list[dict] = []
        self.cart_puts: list[dict] = []
        self.searches: list[dict] = []  # POST /api/run/picks/search bodies
        self.reopens: list[dict] = []  # POST /api/run/picks/reopen bodies
        self.retries = 0
        self.photos = 0

    def reset(self):
        self.state = "idle"
        self.message = None
        self.items = [dict(i) for i in self.initial_items]
        self.pick_at = 0
        self.reopen_at: int | None = None  # the item sent back from the summary
        self.skipped: list[str] = []
        self.lines = [
            {"id": i, "cand": c, "items": n, "quantity": dict(q), "flags": list(f)}
            for i, c, n, q, f in DRAFT
        ]
        # one row per item of the draft, in list order (what the summary shows)
        self.rows = [
            {"index": k, "line": i, "item": name}
            for k, (i, name) in enumerate(
                (i, name) for i, _c, names, _q, _f in DRAFT for name in names
            )
        ]

    # -- events and states ---------------------------------------------------------
    def emit(self, kind, data):
        with self.lock:
            seq = len(self.events) + 1
            self.events.append({"seq": seq, "state": self.state, "kind": kind, "data": data})

    def go(self, state, message=None):
        with self.lock:
            self.state, self.message = state, message
            self.emit("state", {"state": state})

    def need(self, *states):
        if self.state not in states:
            raise WrongState

    def later(self, fn, delay=0.05):
        threading.Timer(delay, fn).start()

    def gate(self, name, fn):
        """Run fn soon, or when the test releases `name` if it is held."""
        with self.lock:
            if name in self.hold and name not in self.released:
                self.pending[name] = fn
            else:
                self.later(fn)

    def release(self, name):
        with self.lock:
            self.released.add(name)
            fn = self.pending.pop(name, None)
        if fn:
            self.later(fn)

    # -- the script ------------------------------------------------------------------------
    def start_run(self, filenames):
        self.reset()
        self.photos = len(filenames)
        self.uploads.extend(filenames)
        self.go("reading_list")
        self.gate(
            "reading_list", lambda: self._if("reading_list", lambda: self.go("reviewing_list"))
        )

    def _if(self, state, fn):
        with self.lock:
            if self.state == state:
                fn()

    def confirm_list(self):
        self.go("syncing_history")
        self.gate("syncing_history", lambda: self._if("syncing_history", self._search))

    def _search(self):
        self.emit("history", {"new": 2, "skipped": 0, "stored": 5})
        self.go("searching")
        for n, item in enumerate(self.items, start=1):
            self.emit(
                "search",
                {"i": n, "n": len(self.items), "term": item["search_term"], "found": 15 - n},
            )
        self.gate("searching", lambda: self._if("searching", self._decide))

    def _decide(self):
        self.go("deciding")
        self.emit("decide", {"phase": "start", "accepted": 0, "to_pick": 0})
        self.gate("deciding", lambda: self._if("deciding", self._to_picking))

    def _to_picking(self):
        self.emit("decide", {"phase": "end", "accepted": 1, "to_pick": len(self.picks)})
        self.go("picking")

    def draft(self):
        lines = []
        for ln in self.lines:
            cand, q = ln["cand"], ln["quantity"]
            if cand["unit_of_sale"] == "kg":
                kg = q["value"] / 1000 if q["unit"] == "g" else q["value"]
                clicks = max(1, round(kg / cand["step_kg"]))
                amount = clicks * cand["step_kg"]
            else:
                clicks = max(1, round(q["value"]))
                amount = clicks
            lines.append(
                {
                    "line_id": ln["id"],
                    "product": cand,
                    "items": ln["items"],
                    "quantity": q,
                    "clicks": clicks,
                    "flags": ln["flags"],
                    "estimated_price": f"{float(cand['price']) * amount:.2f}",
                }
            )
        total = sum(float(ln["estimated_price"]) for ln in lines) if lines else None
        return {
            "lines": lines,
            "skipped": list(self.skipped),
            "estimated_total": None if total is None else f"{total:.2f}",
        }

    def summary(self):
        live = {ln["id"]: ln for ln in self.lines}
        out = []
        for row in self.rows:
            ln = live.get(row["line"])
            if ln is None:
                out.append(
                    {
                        "index": row["index"],
                        "item": row["item"],
                        "state": "removed",
                        "product": None,
                        "quantity": None,
                        "flags": [],
                        "estimated_price": None,
                        "merged": False,
                    }
                )
                continue
            share = len(ln["items"])
            drafted = next(d for d in self.draft()["lines"] if d["line_id"] == ln["id"])
            out.append(
                {
                    "index": row["index"],
                    "item": row["item"],
                    "state": "in_cart",
                    "product": ln["cand"],
                    "quantity": {**ln["quantity"], "value": ln["quantity"]["value"] / share},
                    "flags": ln["flags"],
                    "estimated_price": float(drafted["estimated_price"]) / share,
                    "merged": share > 1,
                }
            )
        for k, name in enumerate(self.skipped):
            out.append(
                {
                    "index": 100 + k,
                    "item": name,
                    "state": "skipped",
                    "product": None,
                    "quantity": None,
                    "flags": [],
                    "estimated_price": None,
                    "merged": False,
                }
            )
        return out

    def reopen(self, index):
        self.reopen_at = index
        self.go("picking")

    def reopened_pick(self):
        row = next(r for r in self.summary() if r["index"] == self.reopen_at)
        mine = row["product"] or _candidate("x-1", "Produto antigo", "3.00")
        other = _candidate("x-2", "Outro produto", "4.00")
        quantity = row["quantity"] or {"value": 1, "unit": "un"}
        return {
            "index": self.reopen_at,
            "left": 1,
            "position": 1,
            "total": 1,
            "reopened": True,
            "item": {**LIST_ITEMS[0], "name": row["item"], "search_term": row["item"]},
            "candidates": [mine, other],
            "jev": {
                "choice": mine["product_id"] if row["product"] else None,
                "confidence": None,
                "nothing_fit": False,
            },
            "quantities": {c["product_id"]: {**quantity, "flags": []} for c in (mine, other)},
        }

    def confirm_cart(self):
        self.go("filling_cart")
        n = len(self.lines)
        self.emit(
            "fill",
            {
                "i": 1,
                "n": n,
                "name": self.lines[0]["cand"]["name"],
                "status": "added",
                "message": None,
            },
        )
        self.gate("filling_cart", lambda: self._if("filling_cart", self._finish))

    def _finish(self):
        n = len(self.lines)
        for i, ln in enumerate(self.lines[1:], start=2):
            self.emit(
                "fill",
                {
                    "i": i,
                    "n": n,
                    "name": ln["cand"]["name"],
                    "status": "added" if i < n else "failed",
                    "message": None if i < n else "botão não apareceu",
                },
            )
        self.go("done")

    def retry(self):
        self.retries += 1
        self.go("filling_cart")
        self.emit(
            "fill",
            {"i": 1, "n": 2, "name": "Tomate italiano kg", "status": "added", "message": None},
        )
        self.gate("retry", lambda: self._if("filling_cart", lambda: self.go("done")))

    def snapshot(self):
        body = {"state": self.state, "run_id": 1, "message": self.message}
        if self.photos:
            body["photos"] = self.photos
        if self.state == "idle":
            return {"state": "idle"}
        if self.state == "reviewing_list":
            body["list"] = {"items": self.items}
        if self.state == "picking":
            body["picks_left"] = len(self.picks) - self.pick_at
        if self.state == "reviewing_cart":
            body["cart_draft"] = self.draft()
            body["summary"] = self.summary()
        if self.state == "done":
            body["outcome"] = OUTCOME_AFTER_RETRY if self.retries else OUTCOME
        return body


def create_app(stub: Stub) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(WrongState)
    async def _wrong_state(request, exc):
        return JSONResponse({"state": stub.state}, status_code=409)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/stub-img/{name}.svg")
    def image(name: str):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
            '<rect width="96" height="96" fill="#cfe3ff"/>'
            f'<text x="8" y="52" font-size="14">{name}</text></svg>'
        )
        return Response(svg, media_type="image/svg+xml")

    @app.get("/api/run")
    def get_run():
        stub.snapshots += 1
        return stub.snapshot()

    @app.get("/api/run/events")
    async def events(request: Request, after: int = 0):
        if stub.events_down:
            return Response(status_code=503)
        stub.stream_opens.append(after)

        async def gen():
            seen = after
            yield ": ok\n\n"
            while not stub.closing and not await request.is_disconnected():
                with stub.lock:
                    fresh = [e for e in stub.events if e["seq"] > seen]
                for event in fresh:
                    seen = event["seq"]
                    yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/run", status_code=202)
    async def post_run(photos: list[UploadFile]):
        with stub.lock:
            stub.need("idle")
            stub.start_run([photo.filename or "photo" for photo in photos])
        return {"run_id": 1}

    @app.put("/api/run/list")
    async def put_list(request: Request):
        body = await request.json()
        with stub.lock:
            stub.need("reviewing_list")
            stub.list_puts.append(body)
            stub.items = body["items"]
        return {}

    @app.post("/api/run/list/confirm", status_code=202)
    def confirm_list():
        with stub.lock:
            stub.need("reviewing_list")
            stub.confirm_list()
        return {}

    @app.get("/api/run/picks/next")
    def picks_next():
        with stub.lock:
            stub.need("picking")
            if stub.reopen_at is not None:
                return stub.reopened_pick()
            pick = stub.picks[stub.pick_at]
            return {
                "index": stub.pick_at,
                "left": len(stub.picks) - stub.pick_at,
                "position": stub.pick_at + 1,
                "total": len(stub.picks),
                "reopened": False,
                **pick,
            }

    @app.post("/api/run/picks")
    async def post_pick(request: Request):
        body = await request.json()
        with stub.lock:
            stub.need("picking")
            if stub.reopen_at is not None:
                if body.get("index") != stub.reopen_at:
                    return JSONResponse({"detail": "índice errado", "state": stub.state}, 409)
                stub.picks_posted.append(body)
                quantity = body.get("quantity")
                row = next(r for r in stub.rows if r["index"] == stub.reopen_at)
                for ln in stub.lines:
                    if quantity and ln["id"] == row["line"]:
                        ln["quantity"], ln["flags"] = quantity, []
                stub.reopen_at = None
                stub.go("reviewing_cart")
                return {}
            if body.get("index") != stub.pick_at:
                return JSONResponse({"detail": "índice errado", "state": stub.state}, 409)
            stub.picks_posted.append(body)
            if body.get("product_id") is None:
                stub.skipped.append(stub.picks[stub.pick_at]["item"]["name"])
            stub.pick_at += 1
            if stub.pick_at >= len(stub.picks):
                stub.go("reviewing_cart")
        return {}

    @app.post("/api/run/picks/search")
    async def post_pick_search(request: Request):
        body = await request.json()
        with stub.lock:
            stub.need("picking")
            if body.get("index") != stub.pick_at:
                return JSONResponse({"detail": "índice errado"}, 422)
            stub.searches.append(body)
            pick = stub.picks[stub.pick_at]
            stub.picks[stub.pick_at] = {
                **pick,
                "item": {**pick["item"], "search_term": body["term"]},
                "candidates": SEARCH_FOUND,
                "no_results": False,
                "quantities": {
                    c["product_id"]: {"value": 1, "unit": "un", "flags": ["QUANTITY_ASSUMED"]}
                    for c in SEARCH_FOUND
                },
            }
        return {"found": len(SEARCH_FOUND)}

    @app.post("/api/run/picks/reopen")
    async def post_pick_reopen(request: Request):
        body = await request.json()
        with stub.lock:
            stub.need("reviewing_cart")
            stub.reopens.append(body)
            stub.reopen(body["index"])
        return {"state": "picking"}

    @app.get("/api/run/report")
    def get_report():
        with stub.lock:
            if stub.state == "idle":
                raise WrongState
        return REPORT

    @app.put("/api/run/cart-draft")
    async def put_cart(request: Request):
        body = await request.json()
        with stub.lock:
            stub.need("reviewing_cart")
            stub.cart_puts.append(body)
            for edit in body["lines"]:
                for ln in list(stub.lines):
                    if ln["id"] != edit["line_id"]:
                        continue
                    if edit.get("remove"):
                        stub.lines.remove(ln)
                    elif edit.get("quantity"):
                        ln["quantity"] = edit["quantity"]
            return {"cart_draft": stub.draft()}

    @app.post("/api/run/cart-draft/confirm", status_code=202)
    def confirm_cart():
        with stub.lock:
            stub.need("reviewing_cart")
            stub.confirm_cart()
        return {}

    @app.post("/api/run/retry", status_code=202)
    def retry():
        with stub.lock:
            stub.need("done")
            stub.retry()
        return {}

    @app.post("/api/run/cancel")
    def cancel():
        with stub.lock:
            stub.need(*(WORKING | WAITING))
            stub.go("cancelled", "Rodada cancelada.")
        return {}

    @app.delete("/api/run")
    def delete_run():
        with stub.lock:
            stub.need("done", "failed", "cancelled")
            stub.go("idle")
        return {}

    @app.get("/api/runs")
    def runs():
        return [
            {"run_id": 7, "created_at": "2026-09-30T18:20:00", "items": 12, "status": "done"},
            {"run_id": 6, "created_at": "2026-09-28T09:05:00", "items": 1, "status": "cancelled"},
        ]

    @app.get("/api/preferences")
    def list_prefs():
        return [{"key": key, **entry} for key, entry in stub.prefs.items()]

    @app.put("/api/preferences/{key:path}")
    async def put_pref(key: str, request: Request):
        entry = await request.json()
        norm = key.casefold().replace("ã", "a").replace("á", "a")  # enough for the tests
        with stub.lock:
            stub.prefs[norm] = entry
        return {"key": norm, **entry}

    @app.delete("/api/preferences/{key:path}")
    def delete_pref(key: str):
        with stub.lock:
            stub.prefs.pop(key, None)
        return {"deleted": True}

    @app.get("/api/access")
    def access():
        if not stub.access:
            return JSONResponse({"detail": "só no desktop"}, 403)
        return {
            "url": "http://192.168.0.10:8000/?t=stubtoken",
            "qr_svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect width="5" height="5"/></svg>',
        }

    @app.post("/_stub/release/{name}")
    def release(name: str):
        stub.release(name)
        return {}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


class StubServer:
    """The stub app on 127.0.0.1, run by uvicorn in a thread. Use as a context manager."""

    def __init__(self, **options):
        self.stub = Stub(**options)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        config = uvicorn.Config(
            create_app(self.stub),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            timeout_graceful_shutdown=1,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("the stub server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.stub.closing = True
        self.server.should_exit = True
        self.thread.join(timeout=5)
