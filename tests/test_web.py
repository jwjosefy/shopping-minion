"""The web app with fakes: no browser, no Jev, no claude, no site."""

import asyncio
import contextlib
import json
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from shopping_minion import cli
from shopping_minion.browser import NotLoggedInError
from shopping_minion.history import ItemHistory
from shopping_minion.items import Candidate, CartResult, Decision, Item, Quantity
from shopping_minion.merge import line_label
from shopping_minion.orders import SyncResult
from shopping_minion.reconcile import expected_amount, format_amount, reconcile
from shopping_minion.storage import Storage
from shopping_minion.web.app import (
    Access,
    create_app,
    is_loopback,
    lan_ip,
    make_access,
    qr_svg,
    sse_stream,
)
from shopping_minion.web.statemachine import RunStateMachine
from shopping_minion.workflow import CartOutcome


def cand(pid, name, unit="un", step_kg=None, price="10.00", image=None):
    return Candidate(
        product_id=pid,
        slug=f"slug-{pid}",
        name=name,
        brand="Marca",
        price=Decimal(price),
        list_price=None,
        unit_of_sale=unit,
        step_kg=step_kg,
        available=True,
        image=image,
    )


def item(name, quantity=None):
    return Item(source_line=name, name=name, search_term=name, quantity=quantity)


FRANGO = cand("1", "Filé de Frango kg", unit="kg", step_kg=0.1, price="27.99", image="http://img/1")
ATUM_A = cand("2", "Atum Gomes 170g", price="13.98")
ATUM_B = cand("3", "Atum Coqueiro 170g", price="12.00")
FEIJAO = cand("4", "Feijão Camil 1kg", price="8.00")

LIST = [
    item("frango", Quantity(value=1, unit="kg")),
    item("atum"),
    item("feijão preto"),
    item("sal"),
]
RESULTS = {"frango": [FRANGO], "atum": [ATUM_A, ATUM_B], "feijão preto": [FEIJAO], "sal": []}
# name -> (choice, confidence, status)
DECISIONS = {
    "frango": ("1", 0.95, "accepted"),
    "atum": ("3", 0.6, "ask"),
    "feijão preto": (None, 0.9, "no_match"),
    "sal": (None, None, "no_match"),
}


class World:
    """The fakes, and what they saw."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.logged_in = True
        self.opened = 0
        self.closed = 0
        self.searched = 0
        self.items_seen = None
        self.transcribe_error = None
        self.fill_gate: threading.Event | None = None  # when set, fill waits after a product
        self.fill_gate_at = 1  # ... after this one (1-based)
        self.fill_started = threading.Event()
        self.cart_before = [("Leite UHT", "1")]
        self.photos_seen: list[tuple[str, bytes]] = []  # what the OCR was given, in order
        self.synced = []  # first_n of each sync
        self.sync_result = SyncResult(new=2, skipped=1, stored=5)
        self.histories_seen = None  # what decide and draft got
        self.draft_histories = None
        self.config_seen = None
        self.fail_ids: set[str] = set()  # lines whose add the site won't take, until retried
        self.fills: list[list[str]] = []  # the line ids of each fill call
        self.searched_one: list[str] = []  # the terms searched from the picker
        self.search_one_results: dict[str, list[Candidate]] = {}
        self.search_one_error: str | None = None

    # injected functions
    def transcribe(self, photos):
        self.photos_seen = [(p.name, p.read_bytes()) for p in photos]
        if self.transcribe_error:
            raise RuntimeError(self.transcribe_error)
        return list(LIST)

    @contextlib.contextmanager
    def open_browser(self):
        self.opened += 1
        try:
            yield None, SimpleNamespace(new_page=lambda: "page")
        finally:
            self.closed += 1

    def ensure_logged_in(self, page):
        assert page == "page"
        if not self.logged_in:
            raise NotLoggedInError("Not logged in")

    def search_one(self, page, item):
        """The picker's own search (a new term for one item)."""
        assert page == "page"
        self.searched_one.append(item.search_term)
        if self.search_one_error:
            raise RuntimeError(self.search_one_error)
        return list(self.search_one_results.get(item.search_term, []))

    def search(self, page, items, progress):
        self.searched += 1
        self.items_seen = items
        out = []
        for i, it in enumerate(items, start=1):
            found = RESULTS[it.name]
            out.append(found)
            progress(i, len(items), it, found)
        return out

    def sync(self, page, storage, first_n, progress=None):
        assert page == "page"
        self.synced.append(first_n)
        return self.sync_result

    def decide(self, items, candidates, prefs, config, client, histories=None):
        assert client == "jev-client"
        self.histories_seen = histories
        self.config_seen = config
        decisions = []
        for it, found in zip(items, candidates, strict=True):
            choice, confidence, status = DECISIONS[it.name]
            decisions.append(
                Decision(
                    item=it,
                    candidates=found,
                    choice=choice,
                    confidence=confidence,
                    status=status,
                )
            )
        return decisions

    def fill(self, page, draft, progress, should_stop=None):
        results = []
        self.fills.append([line.line_id for line in draft.lines])
        failing = set(self.fail_ids)
        self.fail_ids.clear()  # a retry succeeds
        for i, line in enumerate(draft.lines, start=1):
            if should_stop is not None and should_stop():
                break
            if line.line_id in failing:
                result = CartResult(
                    product_id=line.line_id,
                    status="failed",
                    quantity_shown=None,
                    message="o site não aceitou o clique em Adicionar",
                )
            else:
                result = CartResult(
                    product_id=line.line_id, status="added", quantity_shown=None, message=None
                )
            results.append(result)
            progress(i, len(draft.lines), line.candidate, result)
            if self.fill_gate is not None and i == self.fill_gate_at:
                self.fill_started.set()
                assert self.fill_gate.wait(5)
        after = list(self.cart_before)
        for line in draft.lines[: len(results)]:
            if line.line_id in failing:
                continue
            quantity = format_amount(expected_amount(line.candidate, line.target))
            after.append((line.candidate.name, quantity))
        planned = [(line_label(ln), ln.candidate, ln.target) for ln in draft.lines]
        checks, extras = reconcile(planned, self.cart_before, after)
        return CartOutcome(
            before=self.cart_before,
            results=results,
            after=after,
            checks=checks,
            extras=extras,
            stopped=len(results) < len(draft.lines),
        )


@pytest.fixture
def world(tmp_path):
    (tmp_path / "decide.yaml").write_text(
        "model: jev-latest\nbatch_size: 5\naccept_at: 0.8\nask_below: 0.5\nhistory: options\n"
    )
    (tmp_path / "history.yaml").write_text("first_sync_orders: 7\nrelated_lines: 4\n")
    return World(tmp_path)


def make_machine(world):
    return RunStateMachine(
        db_path=world.tmp_path / "db" / "t.sqlite",
        prefs_path=world.tmp_path / "preferencias.yaml",  # absent: no preferences
        config_path=world.tmp_path / "decide.yaml",
        history_config_path=world.tmp_path / "history.yaml",
        uploads_dir=world.tmp_path / "uploads",
        # lambdas, so a test can swap one of the world's methods after the machine exists
        transcribe_fn=lambda photo: world.transcribe(photo),
        open_browser_fn=lambda: world.open_browser(),
        ensure_logged_in_fn=lambda page: world.ensure_logged_in(page),
        search_fn=lambda *a: world.search(*a),
        search_one_fn=lambda *a: world.search_one(*a),
        decide_fn=lambda *a, **kw: world.decide(*a, **kw),
        fill_fn=lambda *a, **kw: world.fill(*a, **kw),
        sync_fn=lambda *a, **kw: world.sync(*a, **kw),
        client_factory=lambda: "jev-client",
    )


@pytest.fixture
def machine(world):
    return make_machine(world)


@pytest.fixture
def client(machine):
    app = create_app(machine, access=Access(is_loopback=lambda _host: True))
    with TestClient(app) as test_client:
        yield test_client
    machine.shutdown()


def wait_for(client, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    snap = None
    while time.monotonic() < deadline:
        snap = client.get("/api/run").json()
        if snap["state"] == state:
            return snap
        time.sleep(0.01)
    raise AssertionError(f"state stayed {snap['state']!r}, wanted {state!r}: {snap}")


def upload(client):
    return client.post("/api/run", files={"photo": ("lista.jpg", b"fake-jpeg", "image/jpeg")})


def upload_many(client, n, field="photos"):
    files = [(field, (f"p{i}.jpg", b"fake-jpeg", "image/jpeg")) for i in range(n)]
    return client.post("/api/run", files=files)


def events(client, after=0):
    text = client.get(f"/api/run/events?after={after}&follow=false").text
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def to_reviewing_list(client):
    assert upload(client).status_code == 202
    return wait_for(client, "reviewing_list")


def to_picking(client):
    to_reviewing_list(client)
    assert client.post("/api/run/list/confirm").status_code == 202
    return wait_for(client, "picking")


def to_reviewing_cart(client):
    to_picking(client)
    client.post("/api/run/picks", json={"index": 1, "product_id": "3"})
    client.post("/api/run/picks", json={"index": 2, "product_id": None})
    client.post("/api/run/picks", json={"index": 3, "product_id": None})  # sal: no results
    return wait_for(client, "reviewing_cart")


# --- the whole flow ---------------------------------------------------------------------------


def test_the_whole_flow_in_state_order(client, world):
    assert client.get("/api/run").json() == {"state": "idle", "run_id": None, "message": None}
    assert client.get("/api/runs").json() == []

    # upload -> reading_list -> reviewing_list
    response = upload(client)
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    snap = wait_for(client, "reviewing_list")
    assert snap["run_id"] == run_id
    assert [i["name"] for i in snap["list"]] == ["frango", "atum", "feijão preto", "sal"]
    assert (world.tmp_path / "uploads" / f"{run_id}-1.jpg").read_bytes() == b"fake-jpeg"
    assert snap["photos"] == 1
    assert client.get("/api/run/photo").content == b"fake-jpeg"

    # PUT list keeps the edits across a reload
    edited = [i.model_dump(mode="json") for i in LIST]
    edited[1]["constraints"] = ["em lata"]
    assert client.put("/api/run/list", json={"items": edited}).status_code == 200
    assert client.get("/api/run").json()["list"][1]["constraints"] == ["em lata"]

    # confirm -> searching -> deciding -> picking
    assert client.post("/api/run/list/confirm").status_code == 202
    snap = wait_for(client, "picking")
    assert snap["picks_left"] == 3
    assert world.opened == 1 and world.closed == 0  # the window stays open

    first = client.get("/api/run/picks/next").json()
    assert (first["index"], first["left"], first["position"], first["total"]) == (1, 3, 1, 3)
    assert first["item"]["name"] == "atum"
    assert [c["product_id"] for c in first["candidates"]] == ["3", "2"]  # Jev's pick first
    assert first["candidates"][0]["description"].startswith("Atum Coqueiro 170g, marca Marca")
    assert first["candidates"][0]["price"] == 12.0
    assert first["jev"] == {"choice": "3", "confidence": 0.6, "nothing_fit": False}
    assert first["no_results"] is False

    assert client.post("/api/run/picks", json={"index": 1, "product_id": "2"}).status_code == 200
    second = client.get("/api/run/picks/next").json()
    assert (second["index"], second["left"], second["position"]) == (2, 2, 2)
    assert second["jev"] == {"choice": None, "confidence": 0.9, "nothing_fit": False}
    assert client.post("/api/run/picks", json={"index": 2, "product_id": None}).status_code == 200
    # sal found nothing, even after the retry: it reaches the picker, with no candidates
    third = client.get("/api/run/picks/next").json()
    assert (third["index"], third["left"], third["position"]) == (3, 1, 3)
    assert third["candidates"] == [] and third["no_results"] is True
    assert client.post("/api/run/picks", json={"index": 3, "product_id": None}).status_code == 200

    # reviewing_cart
    snap = wait_for(client, "reviewing_cart")
    draft = snap["cart_draft"]
    assert [line["line_id"] for line in draft["lines"]] == ["1", "2"]
    frango, atum = draft["lines"]
    assert frango["items"] == ["frango"]
    assert frango["quantity"] == {"value": 1.0, "unit": "kg"}
    assert frango["clicks"] == 10 and frango["flags"] == []
    assert frango["estimated_price"] == 27.99
    assert frango["product"]["image"] == "http://img/1"
    assert atum["flags"] == ["QUANTITY_ASSUMED"] and atum["clicks"] == 1
    assert draft["skipped"] == ["feijão preto", "sal"]
    assert draft["estimated_total"] == pytest.approx(27.99 + 13.98)

    edit = {"lines": [{"line_id": "1", "quantity": {"value": 500, "unit": "g"}, "remove": False}]}
    response = client.put("/api/run/cart-draft", json=edit)
    assert response.status_code == 200
    edited_draft = response.json()["cart_draft"]
    assert edited_draft["lines"][0]["clicks"] == 5
    assert edited_draft["estimated_total"] == pytest.approx(13.995 + 13.98, abs=0.01)
    assert client.get("/api/run").json()["cart_draft"]["lines"][0]["clicks"] == 5

    # confirm -> filling_cart -> done
    assert client.post("/api/run/cart-draft/confirm").status_code == 202
    snap = wait_for(client, "done")
    outcome = snap["outcome"]
    assert outcome["total"] == 2 and outcome["ok_count"] == 2 and outcome["stopped"] is False
    assert outcome["checks"][0] == {
        "item_names": ["frango"],
        "product_name": "Filé de Frango kg",
        "expected": "500g",
        "found": "500g",
        "was_before": False,
        "ok": True,
        "verdict": "ok",
    }
    assert outcome["extras"] == [{"name": "Leite UHT", "quantity": "1", "was_before": True}]
    assert [r["status"] for r in outcome["results"]] == ["added", "added"]
    assert world.closed == 0  # still open on the cart

    # the events, in the order of the LLD
    log = events(client)
    assert [e["data"]["state"] for e in log if e["kind"] == "state"] == [
        "reading_list",
        "reviewing_list",
        "syncing_history",
        "searching",
        "deciding",
        "picking",
        "reviewing_cart",
        "filling_cart",
        "done",
    ]
    assert [e["seq"] for e in log] == sorted({e["seq"] for e in log})
    searches = [e for e in log if e["kind"] == "search"]
    assert [e["data"] for e in searches][:2] == [
        {"i": 1, "n": 4, "term": "frango", "found": 1, "retried": False},
        {"i": 2, "n": 4, "term": "atum", "found": 2, "retried": False},
    ]
    assert {e["state"] for e in searches} == {"searching"}
    decides = [e["data"] for e in log if e["kind"] == "decide"]
    assert decides == [
        {"phase": "start", "accepted": None, "to_pick": None},
        {"phase": "end", "accepted": 1, "to_pick": 3},
    ]
    fills = [e["data"] for e in log if e["kind"] == "fill"]
    assert fills[0] == {
        "i": 1,
        "n": 2,
        "name": "Filé de Frango kg",
        "status": "added",
        "message": None,
    }
    assert len(fills) == 2

    # the history, from SQLite
    (history,) = client.get("/api/runs").json()
    assert history["run_id"] == run_id and history["status"] == "done" and history["items"] == 4
    assert history["created_at"]

    # DELETE closes the browser and goes back to idle
    assert client.delete("/api/run").status_code == 200
    assert world.closed == 1
    assert client.get("/api/run").json()["state"] == "idle"
    assert upload(client).status_code == 202  # and a new run can start


def test_the_run_log_has_states_picks_cart_edits_and_the_check(client, world, monkeypatch):
    monkeypatch.setitem(RESULTS, "sal", [ATUM_A])
    monkeypatch.setitem(DECISIONS, "sal", ("2", 0.6, "ask"))
    to_picking(client)
    # atum: Jev said "3" -> confirm; feijão preto: Jev said none -> another; sal: skip
    client.post("/api/run/picks", json={"index": 1, "product_id": "3"})
    client.post("/api/run/picks", json={"index": 2, "product_id": "4"})
    client.post("/api/run/picks", json={"index": 3, "product_id": None})
    wait_for(client, "reviewing_cart")
    edits = [
        {"line_id": "1", "quantity": {"value": 500, "unit": "g"}, "remove": False},
        {"line_id": "4", "quantity": None, "remove": True},
    ]
    assert client.put("/api/run/cart-draft", json={"lines": edits}).status_code == 200
    client.post("/api/run/cart-draft/confirm")
    wait_for(client, "done")

    db = Storage(world.tmp_path / "db" / "t.sqlite")
    log = db.read_log(1)
    db.close()
    assert [r["seq"] for r in log] == list(range(1, len(log) + 1))
    assert [r["data"]["state"] for r in log if r["kind"] == "state"] == [
        "reading_list",
        "reviewing_list",
        "syncing_history",
        "searching",
        "deciding",
        "picking",
        "reviewing_cart",
        "filling_cart",
        "done",
    ]
    assert [r["data"] for r in log if r["kind"] == "history"] == [
        {"new": 2, "skipped": 1, "stored": 5}
    ]
    assert [r["data"] for r in log if r["kind"] == "decide"] == [
        {
            "model": "jev-latest",
            "history": "options",
            "accept_at": 0.8,
            "ask_below": 0.5,
            "batch_size": 5,
        }
    ]
    assert [r["at"] for r in log] == sorted(r["at"] for r in log)
    assert [r["data"] for r in log if r["kind"] == "pick"] == [
        {"index": 1, "item": "atum", "jev_choice": "3", "jev_confidence": 0.6, "chosen": "3"},
        {
            "index": 2,
            "item": "feijão preto",
            "jev_choice": None,
            "jev_confidence": 0.9,
            "chosen": "4",
        },
        {"index": 3, "item": "sal", "jev_choice": "2", "jev_confidence": 0.6, "chosen": None},
    ]
    assert [r["data"] for r in log if r["kind"] == "cart_edit"] == [
        {"line_id": "1", "quantity": {"value": 500.0, "unit": "g"}, "remove": False},
        {"line_id": "4", "quantity": None, "remove": True},
    ]
    kinds = [r["kind"] for r in log]
    assert kinds.index("check") < len(kinds) - 1  # the check comes before the final state row
    assert [r["data"] for r in log if r["kind"] == "check"] == [
        {"ok_count": 2, "total": 2, "extras": 1, "not_ok": []}
    ]
    # one search row per item, so an empty search can be told apart afterwards
    searches = [r["data"] for r in log if r["kind"] == "search"]
    assert searches and all("found" in s and "term" in s for s in searches)


def test_nothing_to_pick_goes_straight_to_reviewing_cart(client, monkeypatch):
    monkeypatch.setitem(DECISIONS, "atum", ("3", 0.95, "accepted"))
    monkeypatch.setitem(DECISIONS, "feijão preto", ("4", 0.9, "accepted"))
    monkeypatch.setitem(RESULTS, "sal", [ATUM_A])
    monkeypatch.setitem(DECISIONS, "sal", ("2", 0.9, "accepted"))
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    snap = wait_for(client, "reviewing_cart")
    assert [line["line_id"] for line in snap["cart_draft"]["lines"]] == ["1", "3", "4", "2"]
    states = [e["data"]["state"] for e in events(client) if e["kind"] == "state"]
    assert "picking" not in states


def test_a_picked_product_is_saved_as_user_chosen(client, world):
    to_reviewing_cart(client)
    import sqlite3

    con = sqlite3.connect(world.tmp_path / "db" / "t.sqlite")
    rows = [json.loads(r[0]) for r in con.execute("SELECT decision_json FROM decisions")]
    atum = rows[1]
    assert (atum["status"], atum["choice"], atum["confidence"]) == ("user_chosen", "3", None)
    assert rows[2]["status"] == "skipped" and rows[3]["status"] == "skipped"  # sal: no results
    assert con.execute("SELECT status FROM runs").fetchone()[0] == "resolved"


# --- 409 and 4xx ------------------------------------------------------------------------------

CALLS = {
    "upload": lambda c: upload(c),
    "put_list": lambda c: c.put("/api/run/list", json={"items": []}),
    "confirm_list": lambda c: c.post("/api/run/list/confirm"),
    "next_pick": lambda c: c.get("/api/run/picks/next"),
    "pick": lambda c: c.post("/api/run/picks", json={"index": 0, "product_id": None}),
    "put_cart": lambda c: c.put("/api/run/cart-draft", json={"lines": []}),
    "confirm_cart": lambda c: c.post("/api/run/cart-draft/confirm"),
    "pick_search": lambda c: c.post("/api/run/picks/search", json={"index": 1, "term": "x"}),
    "pick_reopen": lambda c: c.post("/api/run/picks/reopen", json={"index": 0}),
    "cancel": lambda c: c.post("/api/run/cancel"),
    "delete": lambda c: c.delete("/api/run"),
}
ALLOWED = {
    "idle": {"upload"},
    "reviewing_list": {"put_list", "confirm_list", "cancel"},
    "picking": {"next_pick", "pick", "pick_search", "cancel"},
    "reviewing_cart": {"put_cart", "confirm_cart", "pick_reopen", "cancel"},
    "done": {"delete"},
}
REACH = {
    "idle": lambda c: None,
    "reviewing_list": to_reviewing_list,
    "picking": to_picking,
    "reviewing_cart": to_reviewing_cart,
}


@pytest.mark.parametrize("state", ["idle", "reviewing_list", "picking", "reviewing_cart"])
def test_every_wrong_state_call_is_409_with_the_state(client, state):
    REACH[state](client)
    for name, call in CALLS.items():
        if name in ALLOWED[state]:
            continue
        response = call(client)
        assert response.status_code == 409, (state, name)
        assert response.json() == {"state": state}, (state, name)
    assert client.get("/api/run").json()["state"] == state  # nothing moved


def test_wrong_state_calls_in_done(client):
    to_reviewing_cart(client)
    client.post("/api/run/cart-draft/confirm")
    wait_for(client, "done")
    for name, call in CALLS.items():
        if name in ALLOWED["done"]:
            continue
        response = call(client)
        assert (response.status_code, response.json()) == (409, {"state": "done"}), name


def test_a_second_run_is_409_and_does_not_disturb_the_first(client):
    to_reviewing_list(client)
    response = upload(client)
    assert response.status_code == 409 and response.json() == {"state": "reviewing_list"}
    assert client.get("/api/run").json()["state"] == "reviewing_list"


def test_a_pick_for_the_wrong_index_or_product_is_422(client):
    to_picking(client)
    wrong_index = client.post("/api/run/picks", json={"index": 2, "product_id": None})
    assert wrong_index.status_code == 422
    assert client.post("/api/run/picks", json={"index": 99, "product_id": None}).status_code == 422
    unknown = client.post("/api/run/picks", json={"index": 1, "product_id": "999"})
    assert unknown.status_code == 422
    assert client.get("/api/run").json()["picks_left"] == 3  # nothing was consumed


def test_bad_bodies_are_rejected(client):
    to_reviewing_list(client)
    assert client.put("/api/run/list", json={"items": [{"name": "x"}]}).status_code == 422
    assert client.post("/api/run").status_code == 400  # no photo: 1 to 5 are expected
    assert client.put("/api/run/list", json={"items": []}).status_code == 200
    assert client.post("/api/run/list/confirm").status_code == 422  # empty list


def test_a_draft_edit_for_an_unknown_line_is_422_and_remove_moves_it_to_skipped(client):
    to_reviewing_cart(client)
    body = {"lines": [{"line_id": "nope", "quantity": None, "remove": True}]}
    assert client.put("/api/run/cart-draft", json=body).status_code == 422
    body = {"lines": [{"line_id": "3", "quantity": None, "remove": True}]}
    draft = client.put("/api/run/cart-draft", json=body).json()["cart_draft"]
    assert [line["line_id"] for line in draft["lines"]] == ["1"]
    assert draft["skipped"] == ["feijão preto", "sal", "atum"]
    body = {"lines": [{"line_id": "1", "quantity": None, "remove": True}]}
    assert client.put("/api/run/cart-draft", json=body).json()["cart_draft"]["lines"] == []
    assert client.post("/api/run/cart-draft/confirm").status_code == 422  # nothing to add


# --- failures and cancel ----------------------------------------------------------------------


def test_not_logged_in_fails_without_searching_and_closes_the_browser(client, world):
    world.logged_in = False
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    snap = wait_for(client, "failed")
    assert snap["message"] == "faça login: `shopping-minion login`"
    assert world.searched == 0
    errors = [e for e in events(client) if e["kind"] == "error"]
    assert errors[0]["data"] == {"message": "faça login: `shopping-minion login`"}
    assert client.delete("/api/run").status_code == 200
    assert world.closed == 1
    assert client.get("/api/run").json()["state"] == "idle"
    status = client.get("/api/runs").json()[0]["status"]
    assert status == "not_logged_in"


def test_an_ocr_error_fails_the_run(client, world):
    world.transcribe_error = "claude saiu com erro"
    upload(client)
    snap = wait_for(client, "failed")
    assert "claude saiu com erro" in snap["message"]
    client.delete("/api/run")
    assert client.get("/api/run").json()["state"] == "idle"


def test_a_search_error_fails_the_run(client, world, monkeypatch):
    def boom(page, items, progress):
        raise RuntimeError("sem resultados")

    monkeypatch.setattr(world, "search", boom)
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    snap = wait_for(client, "failed")
    assert "sem resultados" in snap["message"]
    client.delete("/api/run")
    assert world.closed == 1


def test_a_jev_error_fails_the_run(client, world, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("sem chave")

    monkeypatch.setattr(world, "decide", boom)
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    snap = wait_for(client, "failed")
    assert "sem chave" in snap["message"]


def test_cancel_in_picking_ends_the_run_and_closes_the_browser(client, world):
    to_picking(client)
    assert client.post("/api/run/cancel").json() == {"state": "cancelled"}
    snap = client.get("/api/run").json()
    assert snap["state"] == "cancelled" and snap["message"] == "cancelado"
    deadline = time.monotonic() + 5
    while world.closed == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert world.closed == 1
    assert client.delete("/api/run").status_code == 200


def test_cancel_in_reviewing_list_needs_no_browser(client, world):
    to_reviewing_list(client)
    assert client.post("/api/run/cancel").status_code == 200
    assert client.get("/api/run").json()["state"] == "cancelled"
    assert world.opened == 0
    client.delete("/api/run")
    assert client.get("/api/run").json()["state"] == "idle"


def test_cancel_during_reading_ignores_the_ocr_result(client, world, monkeypatch):
    gate = threading.Event()
    original = world.transcribe

    def slow(photos):
        gate.wait(5)
        return original(photos)

    monkeypatch.setattr(world, "transcribe", slow)
    upload(client)
    assert client.post("/api/run/cancel").status_code == 200
    gate.set()
    time.sleep(0.1)
    assert client.get("/api/run").json()["state"] == "cancelled"
    assert "list" not in client.get("/api/run").json()


def test_cancel_during_the_search_stops_it_and_closes_the_browser(client, world, monkeypatch):
    release = threading.Event()
    inside = threading.Event()
    seen = []

    def slow_search(page, items, progress):
        inside.set()
        assert release.wait(5)
        for i, it in enumerate(items, start=1):
            seen.append(i)
            progress(i, len(items), it, RESULTS[it.name])  # raises once cancelled
        return [RESULTS[it.name] for it in items]

    monkeypatch.setattr(world, "search", slow_search)
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    assert inside.wait(5)
    assert client.post("/api/run/cancel").status_code == 200
    release.set()
    deadline = time.monotonic() + 5
    while world.closed == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert world.closed == 1 and seen == [1]
    assert client.get("/api/run").json()["state"] == "cancelled"


def test_cancel_mid_fill_stops_after_the_current_product(client, world):
    world.fill_gate = threading.Event()
    to_reviewing_cart(client)
    client.post("/api/run/cart-draft/confirm")
    assert world.fill_started.wait(5)  # product 1 added, the fake waits
    assert client.post("/api/run/cancel").json() == {"state": "filling_cart"}
    assert client.get("/api/run").json()["state"] == "filling_cart"  # not yet
    world.fill_gate.set()
    snap = wait_for(client, "cancelled")
    outcome = snap["outcome"]
    assert outcome["stopped"] is True
    assert len(outcome["results"]) == 1
    assert outcome["total"] == 2 and outcome["ok_count"] == 1
    missing = outcome["checks"][1]
    assert missing["ok"] is False and missing["verdict"].startswith("FALTANDO")
    assert world.closed == 0  # the cart stays open to look at
    assert client.delete("/api/run").status_code == 200
    assert world.closed == 1


def test_a_cancel_after_the_last_product_leaves_the_run_done(client, world):
    world.fill_gate = threading.Event()
    world.fill_gate_at = 2  # waits after the last product
    to_reviewing_cart(client)
    client.post("/api/run/cart-draft/confirm")
    assert world.fill_started.wait(5)
    assert client.post("/api/run/cancel").json() == {"state": "filling_cart"}
    world.fill_gate.set()
    snap = wait_for(client, "done")
    assert snap["outcome"]["stopped"] is False


def test_a_fill_error_fails_the_run_and_keeps_the_browser(client, world, monkeypatch):
    def boom(page, draft, progress, should_stop=None):
        raise RuntimeError("a página morreu")

    monkeypatch.setattr(world, "fill", boom)
    to_reviewing_cart(client)
    client.post("/api/run/cart-draft/confirm")
    snap = wait_for(client, "failed")
    assert "a página morreu" in snap["message"]
    assert world.closed == 0
    client.delete("/api/run")
    assert world.closed == 1


# --- access (LLD-M2 5.1) ----------------------------------------------------------------------


def lan_client(
    machine, token="s3cret", url="http://192.168.0.5:8000/?t=s3cret", host="192.168.0.9"
):
    access = Access(token=token, url=url, is_loopback=lambda h: h == "127.0.0.1")
    return TestClient(create_app(machine, access=access), client=(host, 5000))


def test_lan_without_token_is_401(machine):
    with lan_client(machine) as client:
        assert client.get("/api/run").status_code == 401
        assert client.get("/").status_code == 401
        assert client.post("/api/run").status_code == 401
        assert client.get("/api/run?t=wrong").status_code == 401


def test_lan_with_the_token_sets_a_cookie_and_redirects_without_it(machine):
    with lan_client(machine) as client:
        response = client.get("/api/run?t=s3cret&x=1", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/api/run?x=1"
        cookie = response.headers["set-cookie"]
        assert "sm_token=s3cret" in cookie and "HttpOnly" in cookie
        assert "Max-Age=2592000" in cookie and "SameSite=lax" in cookie
        # the client keeps the cookie: no token needed now
        assert client.get("/api/run").json()["state"] == "idle"
        client.cookies.clear()
        assert client.get("/api/run").status_code == 401
        response = client.get("/?t=s3cret", follow_redirects=False)
        assert response.headers["location"] == "/"


def test_a_wrong_cookie_is_401(machine):
    with lan_client(machine) as client:
        client.cookies.set("sm_token", "nope")
        assert client.get("/api/run").status_code == 401


def test_loopback_needs_no_token(machine):
    with lan_client(machine, host="127.0.0.1") as client:
        assert client.get("/api/run").status_code == 200


def test_api_access_answers_only_loopback(machine):
    with lan_client(machine, host="127.0.0.1") as client:
        body = client.get("/api/access").json()
        assert body["url"] == "http://192.168.0.5:8000/?t=s3cret"
        assert body["qr_svg"].lstrip().startswith(("<?xml", "<svg"))
    with lan_client(machine) as client:
        client.cookies.set("sm_token", "s3cret")  # authorised, but not the desktop
        assert client.get("/api/access").status_code == 403


def test_local_mode_has_no_token_and_no_qr(machine):
    access = make_access(local=True, port=8000)
    assert access.token is None and access.url is None
    with TestClient(create_app(machine, access=access), client=("127.0.0.1", 1)) as client:
        assert client.get("/api/access").json() == {"url": None, "qr_svg": None}
        assert client.get("/api/run").status_code == 200
    with TestClient(create_app(machine, access=access), client=("192.168.0.9", 1)) as client:
        assert client.get("/api/run").status_code == 401  # nobody else gets in


def test_a_form_from_another_site_is_refused_even_on_loopback(machine):
    with lan_client(machine, host="127.0.0.1") as client:
        response = client.post("/api/run/cancel", headers={"Origin": "http://evil.example"})
        assert response.status_code == 403
        same = client.post("/api/run/cancel", headers={"Origin": "http://testserver"})
        assert same.status_code == 409  # allowed through; idle has nothing to cancel


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("::ffff:127.0.0.1")
    assert not is_loopback("192.168.0.9") and not is_loopback("testclient")
    assert not is_loopback(None)


def test_make_access_builds_a_random_token_url(tmp_path):
    a = make_access(local=False, port=8123, ip="192.168.0.5", token_file=tmp_path / "t1")
    b = make_access(local=False, port=8123, ip="192.168.0.5", token_file=tmp_path / "t2")
    assert a.url == f"http://192.168.0.5:8123/?t={a.token}"
    assert len(a.token) >= 32 and a.token != b.token


def test_make_access_keeps_the_token_across_starts_until_asked_for_a_new_one(tmp_path):
    file = tmp_path / "data" / "web-token"
    first = make_access(local=False, port=8000, ip="192.168.0.5", token_file=file)
    assert file.stat().st_mode & 0o777 == 0o600
    again = make_access(local=False, port=8000, ip="192.168.0.5", token_file=file)
    assert again.token == first.token
    rotated = make_access(local=False, port=8000, ip="192.168.0.5", token_file=file, new_token=True)
    assert rotated.token != first.token
    assert make_access(local=False, port=8000, ip="192.168.0.5", token_file=file).token == (
        rotated.token
    )
    assert file.stat().st_mode & 0o777 == 0o600


def test_token_file_with_loose_mode_is_tightened_on_rotation(tmp_path):
    file = tmp_path / "web-token"
    file.write_text("old")
    file.chmod(0o644)
    make_access(local=False, port=8000, ip="192.168.0.5", token_file=file, new_token=True)
    assert file.stat().st_mode & 0o777 == 0o600


def test_local_access_writes_no_token_file(tmp_path):
    file = tmp_path / "web-token"
    assert make_access(local=True, port=8000, token_file=file).token is None
    assert not file.exists()


def test_make_access_without_a_lan_address(monkeypatch):
    monkeypatch.setattr("shopping_minion.web.app.lan_ip", lambda: None)
    assert make_access(local=False, port=8000) is None


def test_lan_ip_is_a_non_loopback_address_or_none():
    ip = lan_ip()
    assert ip is None or (ip.count(".") == 3 and not is_loopback(ip))


def test_qr_svg_is_an_svg():
    assert "<svg" in qr_svg("http://192.168.0.5:8000/?t=abc")


# --- serve ------------------------------------------------------------------------------------


def test_serve_local_binds_127_0_0_1(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    cli.main(["serve", "--local", "--port", "8123"])
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8123 and seen["workers"] == 1
    assert "?t=" not in capsys.readouterr().out


def test_serve_lan_prints_the_url_and_a_qr(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr("shopping_minion.web.app.lan_ip", lambda: "192.168.0.5")
    cli.main(["serve"])
    out = capsys.readouterr().out
    assert seen["host"] == "0.0.0.0" and seen["port"] == 8000
    assert "http://192.168.0.5:8000/?t=" in out
    assert "█" in out or "▀" in out or "▄" in out  # the QR, as text


def test_serve_without_a_lan_address_exits_1(monkeypatch):
    monkeypatch.setattr("shopping_minion.web.app.lan_ip", lambda: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve"])
    assert exc.value.code == 1


# --- server-sent events -----------------------------------------------------------------------


def test_events_after_a_seq_only_returns_the_newer_ones(client):
    to_reviewing_list(client)
    everything = events(client)
    assert [e["kind"] for e in everything] == ["state", "state"]
    assert events(client, after=everything[0]["seq"]) == everything[1:]
    assert events(client, after=everything[-1]["seq"]) == []
    response = client.get("/api/run/events?follow=false")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("data: {")


def test_events_keep_their_seq_across_runs_and_a_stale_cursor_starts_over(client):
    to_reviewing_list(client)
    last = events(client)[-1]["seq"]
    client.post("/api/run/cancel")
    client.delete("/api/run")
    upload(client)
    wait_for(client, "reviewing_list")
    fresh = events(client)
    assert fresh[0]["seq"] > last  # the log restarted, the numbers did not
    assert [e["seq"] for e in events(client, after=last)] == [e["seq"] for e in fresh]
    assert events(client, after=10_000) == fresh  # cursor from another server life


def test_the_stream_follows_new_events_and_sends_keepalives(machine, world):
    got = []

    async def read():
        async for chunk in sse_stream(machine, 0, poll=0.01, keepalive=0.05):
            got.append(chunk)
            if '"reviewing_list"' in chunk and chunk.startswith("data:"):
                return

    def start_later():
        time.sleep(0.15)  # no event yet: the stream has to wait and send a comment
        machine.start([(b"fake-jpeg", ".jpg")])

    threading.Thread(target=start_later).start()
    asyncio.run(asyncio.wait_for(read(), 5))
    assert ": keepalive\n\n" in got
    payloads = [json.loads(c[6:]) for c in got if c.startswith("data: ")]
    assert [p["data"]["state"] for p in payloads] == ["reading_list", "reviewing_list"]
    assert got[-1].endswith("\n\n")
    machine.shutdown()


# --- static -----------------------------------------------------------------------------------


def test_the_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200 and "<html" in response.text


# --- the order history (LLD-M4 section 13) ----------------------------------------------------


def state_and_kind_sequence(client):
    return [(e["kind"], e["data"].get("state")) for e in events(client) if e["kind"] != "search"]


def test_the_sync_runs_before_the_search_and_emits_history(client, world):
    to_picking(client)
    assert world.synced == [7]  # first_sync_orders from config/history.yaml
    sequence = state_and_kind_sequence(client)
    assert sequence.index(("state", "syncing_history")) < sequence.index(("history", None))
    assert sequence.index(("history", None)) < sequence.index(("state", "searching"))
    (history,) = [e for e in events(client) if e["kind"] == "history"]
    assert history["state"] == "syncing_history"
    assert history["data"] == {"new": 2, "skipped": 1, "stored": 5}


def test_a_sync_failure_emits_the_error_and_the_run_goes_on(client, world, monkeypatch):
    def boom(page, storage, first_n, progress=None):
        raise RuntimeError("a página mudou")

    monkeypatch.setattr(world, "sync", boom)
    to_picking(client)
    (history,) = [e for e in events(client) if e["kind"] == "history"]
    assert history["data"] == {"error": "a página mudou"}
    assert world.searched == 1
    db = Storage(world.tmp_path / "db" / "t.sqlite")
    log = db.read_log(1)
    db.close()
    assert [r["data"] for r in log if r["kind"] == "history"] == [{"error": "a página mudou"}]
    assert [r["data"]["state"] for r in log if r["kind"] == "state"][2:5] == [
        "syncing_history",
        "searching",
        "deciding",
    ]


def test_cancel_during_the_sync_ends_the_run_and_closes_the_browser(client, world, monkeypatch):
    release = threading.Event()
    inside = threading.Event()

    def slow_sync(page, storage, first_n, progress=None):
        inside.set()
        assert release.wait(5)
        progress(1, 1, None)  # raises once cancelled
        return world.sync_result

    monkeypatch.setattr(world, "sync", slow_sync)
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    assert inside.wait(5)
    assert client.get("/api/run").json()["state"] == "syncing_history"
    assert client.post("/api/run/cancel").status_code == 200
    release.set()
    deadline = time.monotonic() + 5
    while world.closed == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert world.closed == 1 and world.searched == 0
    assert client.get("/api/run").json()["state"] == "cancelled"
    assert not [e for e in events(client) if e["kind"] == "history"]


def test_cancel_after_the_sync_returned_still_skips_the_search(client, world, monkeypatch):
    release = threading.Event()
    inside = threading.Event()

    def slow_sync(page, storage, first_n, progress=None):
        inside.set()
        assert release.wait(5)
        return world.sync_result  # never calls progress

    monkeypatch.setattr(world, "sync", slow_sync)
    to_reviewing_list(client)
    client.post("/api/run/list/confirm")
    assert inside.wait(5)
    client.post("/api/run/cancel")
    release.set()
    deadline = time.monotonic() + 5
    while world.closed == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert world.closed == 1 and world.searched == 0


def test_histories_reach_decide_and_the_draft(client, world, monkeypatch):
    from shopping_minion.web import statemachine

    drafted = []
    real = statemachine.draft_cart

    def spy(decisions, prefs, histories=None, *args):
        drafted.append(histories)
        return real(decisions, prefs, histories, *args)

    monkeypatch.setattr(statemachine, "draft_cart", spy)
    to_reviewing_cart(client)
    assert world.config_seen.history == "options"
    assert len(world.histories_seen) == len(LIST)  # one per item, empty tables
    assert all(isinstance(h, ItemHistory) for h in world.histories_seen)
    assert all(h.products == {} and h.related == [] for h in world.histories_seen)
    assert drafted == [world.histories_seen]  # the same objects, built once


# --- empty searches and "tentar de novo" (LLD-M5 sections 1.1 and 1.3) -----------------------


def test_a_retried_search_is_logged_and_empty_items_reach_the_picker(client, world):
    from shopping_minion.search import SearchResults

    def search(page, items, progress):
        out = []
        for i, it in enumerate(items, start=1):
            found = SearchResults(RESULTS[it.name])
            found.retried = it.name in ("sal", "atum")
            out.append(found)
            progress(i, len(items), it, found)
        return out

    world.search = search
    to_picking(client)
    retried = {
        e["data"]["term"]: e["data"]["retried"] for e in events(client) if e["kind"] == "search"
    }
    assert retried == {"frango": False, "atum": True, "feijão preto": False, "sal": True}
    rows = Storage(world.tmp_path / "db" / "t.sqlite").read_log(1)
    assert [r["data"]["retried"] for r in rows if r["kind"] == "search"] == [
        False,
        True,
        False,
        True,
    ]
    client.post("/api/run/picks", json={"index": 1, "product_id": "3"})
    client.post("/api/run/picks", json={"index": 2, "product_id": None})
    pick = client.get("/api/run/picks/next").json()
    assert (pick["item"]["name"], pick["candidates"], pick["no_results"]) == ("sal", [], True)
    assert client.post("/api/run/picks", json={"index": 3, "product_id": None}).status_code == 200
    draft = wait_for(client, "reviewing_cart")["cart_draft"]
    assert draft["skipped"] == ["feijão preto", "sal"]


def test_the_done_screen_lists_failures_and_retry_fills_only_those(client, world):
    to_reviewing_cart(client)
    world.fail_ids = {"1"}
    client.post("/api/run/cart-draft/confirm")
    snap = wait_for(client, "done")
    outcome = snap["outcome"]
    assert (outcome["ok_count"], outcome["total"]) == (1, 2)
    assert outcome["problems"] == [
        {
            "line_id": "1",
            "item_names": ["frango"],
            "product_name": "Filé de Frango kg",
            "expected": "1kg",
            "found": None,
            "message": "o site não aceitou o clique em Adicionar",
        }
    ]

    assert client.post("/api/run/retry").status_code == 202
    snap = wait_for(client, "done")
    assert world.fills == [["1", "3"], ["1"]]  # the second pass has only the failed line
    assert snap["outcome"]["problems"] == []
    assert (snap["outcome"]["ok_count"], snap["outcome"]["total"]) == (1, 1)
    assert [line["line_id"] for line in snap["cart_draft"]["lines"]] == ["1"]
    assert world.opened == 1 and world.closed == 0  # same browser, still open

    log = Storage(world.tmp_path / "db" / "t.sqlite").read_log(1)
    states = [r["data"]["state"] for r in log if r["kind"] == "state"]
    assert states[-4:] == ["filling_cart", "done", "filling_cart", "done"]
    checks = [r["data"] for r in log if r["kind"] == "check"]
    assert len(checks) == 2 and checks[0]["not_ok"][0]["product"] == "Filé de Frango kg"
    assert checks[1]["not_ok"] == []
    assert [r["data"] for r in log if r["kind"] == "retry"] == [{"lines": ["1"]}]
    import sqlite3

    con = sqlite3.connect(world.tmp_path / "db" / "t.sqlite")
    saved = [json.loads(r[0]) for r in con.execute("SELECT result_json FROM cart")]
    assert sorted((r["product_id"], r["status"]) for r in saved) == [("1", "added"), ("3", "added")]
    assert client.delete("/api/run").status_code == 200  # the worker still takes the close job
    assert world.closed == 1


def test_retry_is_only_for_done_with_something_to_retry(client, world):
    assert client.post("/api/run/retry").status_code == 409  # idle
    to_reviewing_cart(client)
    assert client.post("/api/run/retry").status_code == 409  # reviewing_cart
    client.post("/api/run/cart-draft/confirm")
    wait_for(client, "done")
    assert client.post("/api/run/retry").status_code == 422  # everything is ok
    assert client.get("/api/run").json()["state"] == "done"


@pytest.mark.parametrize(("count", "status"), [(0, 400), (1, 202), (5, 202), (6, 400)])
def test_upload_limit(client, count, status):
    response = upload_many(client, count)
    assert response.status_code == status
    if status == 400:
        assert "1 a 5" in response.json()["detail"]
        assert client.get("/api/run").json()["state"] == "idle"  # no run was started


def test_several_photos_are_read_in_order_and_served_by_index(client, world):
    files = [("photos", (f"p{i}.png", f"page{i}".encode(), "image/png")) for i in range(3)]
    run_id = client.post("/api/run", files=files).json()["run_id"]
    snap = wait_for(client, "reviewing_list")
    assert snap["photos"] == 3
    assert world.photos_seen == [(f"{run_id}-{n}.png", f"page{n - 1}".encode()) for n in (1, 2, 3)]
    assert [client.get(f"/api/run/photo?i={i}").content for i in range(3)] == [
        b"page0",
        b"page1",
        b"page2",
    ]
    assert client.get("/api/run/photo").content == b"page0"  # the default is the first
    assert client.get("/api/run/photo?i=3").status_code == 404
    assert client.get("/api/run/photo?i=-1").status_code == 404
    # the row keeps a JSON list of the paths
    stored = Storage(world.tmp_path / "db" / "t.sqlite").list_runs()[0]["photo"]
    assert json.loads(stored) == [
        str(world.tmp_path / "uploads" / f"{run_id}-{n}.png") for n in (1, 2, 3)
    ]


def test_the_old_single_field_still_uploads(client, world):
    response = upload_many(client, 1, field="photo")
    assert response.status_code == 202
    assert wait_for(client, "reviewing_list")["photos"] == 1
    assert len(world.photos_seen) == 1


# --- one pass: product and quantity together (LLD-M5 section 2.3) -----------------------------


def log_rows(world, kind):
    rows = Storage(world.tmp_path / "db" / "t.sqlite").read_log(1)
    return [r["data"] for r in rows if r["kind"] == kind]


def test_pick_view_quantities_follow_the_list_the_history_and_the_default(
    client, world, monkeypatch
):
    from datetime import date

    from shopping_minion.history import ProductHistory
    from shopping_minion.web import statemachine

    items = [
        item("frango", Quantity(value=1, unit="kg")),
        item("atum"),
        item("feijão preto", Quantity(value=5, unit="un")),
        item("sal"),
    ]
    world.transcribe = lambda photos: items
    last = ProductHistory(
        product_id="2",
        orders=3,
        last_at=date(2026, 9, 1),
        last_quantity=Quantity(value=3, unit="un"),
    )  # the atum of candidate 2 was bought before, 3 at a time; candidate 3 never
    monkeypatch.setattr(
        statemachine,
        "histories_for",
        lambda db, items, candidates, k: [
            ItemHistory(products={"2": last} if it.name == "atum" else {}, related=[])
            for it in items
        ],
    )
    to_picking(client)

    atum = client.get("/api/run/picks/next").json()
    assert atum["quantities"] == {
        "2": {"value": 3, "unit": "un", "flags": ["QUANTITY_FROM_HISTORY"]},  # that candidate's
        "3": {"value": 1, "unit": "un", "flags": ["QUANTITY_ASSUMED"]},  # the default
    }
    client.post("/api/run/picks", json={"index": 1, "product_id": "3"})
    feijao = client.get("/api/run/picks/next").json()
    assert feijao["quantities"] == {"4": {"value": 5, "unit": "un", "flags": []}}  # the list's
    client.post("/api/run/picks", json={"index": 2, "product_id": None})
    sal = client.get("/api/run/picks/next").json()
    assert sal["quantities"] == {}  # no candidates, no quantities


def test_pick_view_quantities_follow_the_preference(client, world):
    (world.tmp_path / "preferencias.yaml").write_text(
        "atum:\n  quantidade: {valor: 2, unidade: un}\n", encoding="utf-8"
    )
    to_picking(client)
    atum = client.get("/api/run/picks/next").json()
    assert atum["quantities"] == {
        "2": {"value": 2, "unit": "un", "flags": []},
        "3": {"value": 2, "unit": "un", "flags": []},
    }


def test_a_pick_with_a_quantity_reaches_the_draft_and_is_logged(client, world):
    to_picking(client)
    body = {"index": 1, "product_id": "3", "quantity": {"value": 4, "unit": "un"}}
    assert client.post("/api/run/picks", json=body).status_code == 200
    client.post("/api/run/picks", json={"index": 2, "product_id": "4"})
    client.post("/api/run/picks", json={"index": 3, "product_id": None})
    draft = wait_for(client, "reviewing_cart")["cart_draft"]
    lines = {line["line_id"]: line for line in draft["lines"]}
    assert (lines["3"]["quantity"], lines["3"]["clicks"], lines["3"]["flags"]) == (
        {"value": 4, "unit": "un"},
        4,
        [],  # the user set it: no flag
    )
    assert lines["4"]["flags"] == ["QUANTITY_ASSUMED"]  # no quantity sent: the draft's own
    assert log_rows(world, "pick_quantity") == [
        {"index": 1, "quantity": {"value": 4, "unit": "un"}}
    ]
    assert [p["chosen"] for p in log_rows(world, "pick")] == ["3", "4", None]  # the pick row stays


def test_a_bad_quantity_is_422_and_a_skip_ignores_it(client, world):
    to_picking(client)
    for quantity in ({"value": 0, "unit": "un"}, {"value": 1, "unit": "caixote"}):
        body = {"index": 1, "product_id": "3", "quantity": quantity}
        assert client.post("/api/run/picks", json=body).status_code == 422
    assert client.get("/api/run").json()["picks_left"] == 3
    body = {"index": 1, "product_id": None, "quantity": {"value": 2, "unit": "un"}}
    assert client.post("/api/run/picks", json=body).status_code == 200
    assert log_rows(world, "pick_quantity") == []


# --- a new search term from the picker --------------------------------------------------------


def test_a_search_from_the_picker_replaces_the_cards_and_logs_a_search_row(client, world):
    world.search_one_results = {"sal grosso": [ATUM_A], "sal fino": []}
    to_picking(client)
    client.post("/api/run/picks", json={"index": 1, "product_id": "3"})
    client.post("/api/run/picks", json={"index": 2, "product_id": None})
    assert client.get("/api/run/picks/next").json()["no_results"] is True  # sal, index 3

    response = client.post("/api/run/picks/search", json={"index": 3, "term": "  sal fino "})
    assert (response.status_code, response.json()) == (200, {"found": 0})
    assert client.get("/api/run/picks/next").json()["no_results"] is True  # still nothing

    response = client.post("/api/run/picks/search", json={"index": 3, "term": "sal grosso"})
    assert (response.status_code, response.json()) == (200, {"found": 1})
    assert world.searched_one == ["sal fino", "sal grosso"]  # in the worker, no Jev call
    pick = client.get("/api/run/picks/next").json()
    assert pick["index"] == 3 and pick["no_results"] is False
    assert pick["item"]["search_term"] == "sal grosso"
    assert [c["product_id"] for c in pick["candidates"]] == ["2"]
    assert pick["jev"] == {"choice": None, "confidence": None, "nothing_fit": False}
    assert pick["quantities"] == {"2": {"value": 1, "unit": "un", "flags": ["QUANTITY_ASSUMED"]}}
    searches = [row for row in log_rows(world, "search") if row.get("picker")]
    assert [(r["term"], r["found"], r["index"]) for r in searches] == [
        ("sal fino", 0, 3),
        ("sal grosso", 1, 3),
    ]

    client.post("/api/run/picks", json={"index": 3, "product_id": "2"})
    draft = wait_for(client, "reviewing_cart")["cart_draft"]
    assert "2" in [line["line_id"] for line in draft["lines"]]
    stored = Storage(world.tmp_path / "db" / "t.sqlite").read_decisions(1)
    assert (stored[3].item.search_term, stored[3].status) == ("sal grosso", "user_chosen")


def test_a_search_from_the_picker_is_for_the_current_item_and_can_fail(client, world):
    to_picking(client)
    assert client.post("/api/run/picks/search", json={"index": 2, "term": "x"}).status_code == 422
    assert client.post("/api/run/picks/search", json={"index": 1, "term": "  "}).status_code == 422
    world.search_one_error = "a página mudou"
    response = client.post("/api/run/picks/search", json={"index": 1, "term": "atum lata"})
    assert response.status_code == 422 and "a página mudou" in response.json()["detail"]
    assert client.get("/api/run").json()["state"] == "picking"  # the run goes on
    assert [c["product_id"] for c in client.get("/api/run/picks/next").json()["candidates"]] == [
        "3",
        "2",
    ]  # the cards are as they were


# --- the summary and "trocar" -----------------------------------------------------------------


def summary_of(client):
    return {row["index"]: row for row in client.get("/api/run").json()["summary"]}


def test_the_summary_has_a_row_per_item_and_trocar_reopens_one(client, world):
    to_reviewing_cart(client)
    rows = summary_of(client)
    assert [rows[i]["state"] for i in range(4)] == ["in_cart", "in_cart", "skipped", "skipped"]
    assert rows[0]["item"] == "frango" and rows[0]["product"]["product_id"] == "1"
    assert rows[1]["quantity"] == {"value": 1, "unit": "un"}
    assert rows[1]["flags"] == ["QUANTITY_ASSUMED"]
    assert rows[2]["product"] is None

    # an accepted item can be reopened; it is the only one to pick
    assert client.post("/api/run/picks/reopen", json={"index": 0}).json() == {"state": "picking"}
    pick = client.get("/api/run/picks/next").json()
    assert (pick["index"], pick["left"], pick["position"], pick["total"]) == (0, 1, 1, 1)
    assert pick["reopened"] is True and pick["jev"]["choice"] == "1"
    body = {"index": 0, "product_id": "1", "quantity": {"value": 2, "unit": "kg"}}
    assert client.post("/api/run/picks", json=body).status_code == 200
    snap = wait_for(client, "reviewing_cart")  # back to the summary
    assert {line["line_id"]: line["quantity"] for line in snap["cart_draft"]["lines"]}["1"] == {
        "value": 2,
        "unit": "kg",
    }
    # the report counts the item once, with Jev's own pick kept from the first time
    picks = [p for p in log_rows(world, "pick") if p["index"] == 0]
    assert [(p["jev_choice"], p["chosen"]) for p in picks] == [("1", "1")]
    assert log_rows(world, "reopen") == [{"index": 0}]

    # a skipped item can be reopened too, and then goes into the cart
    client.post("/api/run/picks/reopen", json={"index": 2})
    pick = client.get("/api/run/picks/next").json()
    assert (pick["index"], [c["product_id"] for c in pick["candidates"]]) == (2, ["4"])
    client.post("/api/run/picks", json={"index": 2, "product_id": "4"})
    snap = wait_for(client, "reviewing_cart")
    assert [line["line_id"] for line in snap["cart_draft"]["lines"]] == ["1", "3", "4"]
    assert summary_of(client)[2]["state"] == "in_cart"


def test_reopen_rejects_an_unknown_item(client):
    to_reviewing_cart(client)
    assert client.post("/api/run/picks/reopen", json={"index": 9}).status_code == 422
    assert client.post("/api/run/picks/reopen", json={"index": -1}).status_code == 422
    assert client.get("/api/run").json()["state"] == "reviewing_cart"


def test_edits_from_revisar_tudo_survive_a_trocar(client):
    to_reviewing_cart(client)
    edit = {"line_id": "3", "quantity": {"value": 5, "unit": "un"}, "remove": False}
    client.put("/api/run/cart-draft", json={"lines": [edit]})
    client.put(
        "/api/run/cart-draft", json={"lines": [{"line_id": "1", "quantity": None, "remove": True}]}
    )
    rows = summary_of(client)
    assert rows[0]["state"] == "removed" and rows[0]["product"] is None
    assert rows[1]["quantity"] == {"value": 5, "unit": "un"}

    client.post("/api/run/picks/reopen", json={"index": 2})
    client.post("/api/run/picks", json={"index": 2, "product_id": "4"})
    draft = wait_for(client, "reviewing_cart")["cart_draft"]
    assert {line["line_id"]: line["quantity"]["value"] for line in draft["lines"]} == {
        "3": 5,
        "4": 1,
    }  # the quantity stayed, the removed frango is still out
    assert "frango" in draft["skipped"]


# --- the report on the done screen ------------------------------------------------------------


def test_the_report_route_gives_the_numbers_of_the_current_run(client, world):
    assert client.get("/api/run/report").status_code == 409  # no run
    to_reviewing_cart(client)
    client.post("/api/run/cart-draft/confirm")
    wait_for(client, "done")
    data = client.get("/api/run/report").json()
    assert data["run_id"] == 1 and data["items"] == 4
    states = [step["state"] for step in data["time"]["steps"]]
    assert states[:2] == ["reading_list", "reviewing_list"] and "filling_cart" in states
    assert data["time"]["total"]["seconds"] >= 0 and data["time"]["total"]["text"].endswith(" s")
    assert data["corrections"]["products"]["asked"] == 3
    assert data["corrections"]["products"]["accepted"] == 1
    assert data["check"]["total"] == 2
    # the same data the CLI prints
    from shopping_minion.report import build_report

    db = Storage(world.tmp_path / "db" / "t.sqlite")
    try:
        assert data == build_report(db, 1)
    finally:
        db.close()
