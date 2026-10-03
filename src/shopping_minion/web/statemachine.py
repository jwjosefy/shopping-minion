"""The states of one run, its event log and the worker thread (LLD-M2 section 4).

One run at a time. The photo is read in a plain thread (OCR, no browser). A worker thread
owns the Playwright browser for the whole run, because the sync API must stay on the thread
that created it: it opens the browser at `syncing_history` (login check, then the order
sync), runs the search and the decide pass, then waits on a queue for the cart pass and for the
"close" job. The web layer only calls the public methods below; every one takes the lock,
checks the state and returns or raises `WrongState` (the app turns it into `409 {state}`).

Everything that touches a browser, the network, the model or the disk is injected, so the
tests run the whole flow with fakes.
"""

import contextlib
import json
import logging
import queue
import re
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shopping_minion.browser import NotLoggedInError, ensure_logged_in, open_browser
from shopping_minion.config import (
    DecideConfig,
    HistoryConfig,
    load_decide_config,
    load_history_config,
)
from shopping_minion.decide import nothing_fit
from shopping_minion.history import ItemHistory
from shopping_minion.items import Candidate, CartDraft, CartResult, Decision, Item, Quantity
from shopping_minion.orders import sync_orders
from shopping_minion.preferences import find_preference, load_preferences, normalize_key
from shopping_minion.quantity import target_quantity
from shopping_minion.report import build_report
from shopping_minion.run import (
    _ordered_candidates,
    decide_config_row,
    histories_for,
    learned_for,
)
from shopping_minion.search import search as search_one
from shopping_minion.storage import Storage
from shopping_minion.workflow import (
    CartOutcome,
    DraftEdit,
    ItemRow,
    _total,
    decide_list,
    draft_cart,
    edit_draft,
    fill_cart,
    item_rows,
    problem_indexes,
    search_list,
)

log = logging.getLogger(__name__)

LOGIN_MESSAGE = "faça login: `shopping-minion login`"
JOIN_SECONDS = 60  # how long DELETE waits for the worker to close the browser

WORKING = ("reading_list", "syncing_history", "searching", "deciding", "filling_cart")
WAITING = ("reviewing_list", "picking", "reviewing_cart")
FINISHED = ("done", "failed", "cancelled")


class WrongState(Exception):
    """The call doesn't fit the run's current state (HTTP 409 with the state)."""

    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


MAX_PHOTOS = 5  # pages of one list (LLD-M5 section 2.1)


class BadRequest(ValueError):
    """The call fits the state but its content doesn't (HTTP 422)."""


def _searched_terms(found: Any) -> list[str]:
    """The terms the store was asked for, after the synonyms (the first is the item's own)."""
    return [searched for _written, searched in getattr(found, "terms", [])]


class _Cancelled(Exception):
    """Raised inside the worker's progress callback to end a search the user cancelled."""


@dataclass
class Snapshot:
    state: str
    run_id: int | None
    message: str | None
    items: list[Item] | None
    picks_left: int | None
    draft: CartDraft | None
    outcome: CartOutcome | None
    photos: int = 0  # how many pages the list was read from
    summary: list[ItemRow] | None = None  # reviewing_cart: one row per item


@dataclass
class PickView:
    index: int  # position of the item in the run's list
    left: int  # picks still to make, this one included
    position: int  # 1-based position among the picks of this run
    total: int  # picks in this run
    decision: Decision
    candidates: list[Candidate]  # Jev's pick first
    nothing_fit: bool
    no_results: bool  # the search found nothing, even after the retry
    quantities: dict[str, dict]  # product_id -> {value, unit, flags}, for every candidate
    reopened: bool  # the user came back from the summary to change this item


class _Run:
    def __init__(self, run_id: int, photos: list[Path]) -> None:
        self.id = run_id
        self.photos = photos
        self.message: str | None = None
        self.ocr_items: list[Item] = []
        self.items: list[Item] | None = None
        self.config: DecideConfig | None = None
        self.history_config: HistoryConfig | None = None
        self.histories: list[ItemHistory] | None = None  # one per item, after the search
        self.prefs: dict[str, dict] = {}
        self.decisions: list[Decision] = []
        self.pending: list[int] = []  # indexes into `decisions` still to pick, in order
        self.total_picks = 0
        self.reopened = False  # picking one item again, from the summary
        self.quantities: dict[int, Quantity] = {}  # set by the user, by item index
        self.removed: set[int] = set()  # items taken out in the full editor, by index
        self.jev_picks: dict[int, tuple[str | None, float | None]] = {}  # Jev's own answers
        self.draft: CartDraft | None = None
        self.outcome: CartOutcome | None = None
        self.cart_results: dict[str, CartResult] = {}  # last result per product, across retries
        self.cancel = threading.Event()
        self.jobs: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None


def _describe(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def _transcribe(photos: list[Path]) -> list[Item]:
    from shopping_minion.intake import transcribe

    return transcribe(photos)


def _jev_client() -> Any:
    from typesafe_sdk import TypeSafeClient

    return TypeSafeClient()


class RunStateMachine:
    def __init__(
        self,
        *,
        db_path: str | Path = "data/shopping-minion.sqlite",
        prefs_path: str | Path = "data/preferencias.yaml",
        config_path: str | Path = "config/decide.yaml",
        history_config_path: str | Path = "config/history.yaml",
        uploads_dir: str | Path = "data/uploads",
        transcribe_fn: Callable[[list[Path]], list[Item]] | None = None,
        open_browser_fn: Callable[[], Any] = open_browser,
        ensure_logged_in_fn: Callable[[Any], None] = ensure_logged_in,
        search_fn: Callable[..., list[list[Candidate]]] = search_list,
        search_one_fn: Callable[[Any, Item], list[Candidate]] = search_one,
        decide_fn: Callable[..., list[Decision]] = decide_list,
        fill_fn: Callable[..., CartOutcome] = fill_cart,
        sync_fn: Callable[..., Any] = sync_orders,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._prefs_path = Path(prefs_path)
        self._config_path = Path(config_path)
        self._history_config_path = Path(history_config_path)
        self._uploads_dir = Path(uploads_dir)
        self._transcribe_fn = transcribe_fn or _transcribe
        self._open_browser_fn = open_browser_fn
        self._ensure_logged_in_fn = ensure_logged_in_fn
        self._search_fn = search_fn
        self._search_one_fn = search_one_fn
        self._decide_fn = decide_fn
        self._fill_fn = fill_fn
        self._sync_fn = sync_fn
        self._client_factory = client_factory or _jev_client

        self._lock = threading.RLock()
        self._state = "idle"
        self._run: _Run | None = None
        self._events: list[dict] = []
        self._seq = 0  # never reset, so a client's cursor survives from one run to the next

    # --- reading ----------------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_seq(self) -> int:
        return self._seq

    def snapshot(self) -> Snapshot:
        with self._lock:
            run = self._run
            if run is None:
                return Snapshot(self._state, None, None, None, None, None, None)
            return Snapshot(
                state=self._state,
                run_id=run.id,
                message=run.message,
                items=None if run.items is None else list(run.items),
                picks_left=len(run.pending) if self._state == "picking" else None,
                draft=run.draft,
                outcome=run.outcome,
                photos=len(run.photos),
                summary=self._summary(run) if self._state == "reviewing_cart" else None,
            )

    def events_after(self, seq: int) -> list[dict]:
        with self._lock:
            return [event for event in self._events if event["seq"] > seq]

    # --- preferences: allowed in any state; the next run's decide reads them -----------------

    def list_preferences(self) -> list[dict]:
        with self._db() as db:
            load_preferences(db, self._prefs_path)  # the one-time import, before the first read
            return [{"key": key, **entry} for key, entry in db.read_preferences().items()]

    def set_preference(self, name: str, entry: dict) -> dict:
        key = normalize_key(name)
        if not key:
            raise BadRequest("informe o nome do item")
        entry = {"nome": name.strip(), **entry}  # the name as typed; the key loses accents
        try:
            with self._db() as db:
                load_preferences(db, self._prefs_path)
                db.set_preference(key, entry)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return {"key": key, **entry}

    def delete_preference(self, name: str) -> bool:
        with self._db() as db:
            load_preferences(db, self._prefs_path)
            return db.delete_preference(normalize_key(name))

    def recent_runs(self, limit: int = 5) -> list[dict]:
        with self._db() as db:
            return db.recent_runs(limit)

    def photo_path(self, index: int = 0) -> Path | None:
        with self._lock:
            if self._run is None or not 0 <= index < len(self._run.photos):
                return None
            return self._run.photos[index]

    # --- the run's steps, called by the web layer -------------------------------------------

    def start(self, photos: list[tuple[bytes, str]]) -> int:
        """idle -> reading_list: save the photos (bytes and file suffix, in upload order) and
        read them as one list in a plain thread."""
        if not 1 <= len(photos) <= MAX_PHOTOS:
            raise BadRequest(f"envie de 1 a {MAX_PHOTOS} fotos")
        with self._lock:
            if self._state != "idle":
                raise WrongState(self._state)
            with self._db() as db:
                run_id = db.new_run(photo=None)
                self._uploads_dir.mkdir(parents=True, exist_ok=True)
                paths = []
                for n, (data, suffix) in enumerate(photos, start=1):
                    suffix = suffix.lower()
                    if not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
                        suffix = ".jpg"
                    path = self._uploads_dir / f"{run_id}-{n}{suffix}"
                    path.write_bytes(data)
                    paths.append(path)
                db.set_photo(run_id, [str(p) for p in paths])
            run = _Run(run_id, paths)
            self._run = run
            self._events.clear()
            self._set_state("reading_list")
            threading.Thread(target=self._ocr, args=(run,), daemon=True).start()
            return run_id

    def save_list(self, items: list[Item]) -> None:
        """reviewing_list: keep the edits (SQLite too, so a reload finds them)."""
        with self._lock:
            run = self._require("reviewing_list")
            run.items = list(items)
            with self._db() as db:
                db.save_items(run.id, run.ocr_items, run.items)

    def confirm_list(self) -> None:
        """reviewing_list -> syncing_history: the worker opens the browser, syncs the orders
        and searches."""
        with self._lock:
            run = self._require("reviewing_list")
            if not run.items:
                raise BadRequest("a lista não tem itens")
            self._set_status(run, "items_saved")
            self._set_state("syncing_history")
            run.worker = threading.Thread(target=self._work, args=(run,), daemon=True)
            run.worker.start()

    def next_pick(self) -> PickView:
        with self._lock:
            run = self._require("picking")
            index = run.pending[0]
            decision = run.decisions[index]
            return PickView(
                index=index,
                left=len(run.pending),
                position=run.total_picks - len(run.pending) + 1,
                total=run.total_picks,
                decision=decision,
                candidates=_ordered_candidates(decision),
                nothing_fit=nothing_fit(decision, run.config),
                no_results=not decision.candidates,
                quantities=self._pick_quantities(run, index),
                reopened=run.reopened,
            )

    def _pick_quantities(self, run: _Run, index: int) -> dict[str, dict]:
        """What the draft would use for each candidate (list, preference, that candidate's
        history, 1 un), so the picker shows it before the user chooses. An amount the user set
        for this item earlier (a reopened item) holds for every candidate, with no flag."""
        decision = run.decisions[index]
        pref = find_preference(run.prefs, decision.item)
        histories = run.histories[index].products if run.histories else {}
        out = {}
        for candidate in decision.candidates:
            set_by_user = run.quantities.get(index)
            if set_by_user is not None:
                quantity, flags = set_by_user, []
            else:
                quantity, flags = target_quantity(
                    decision.item,
                    pref,
                    histories.get(candidate.product_id),
                    candidate.unit_of_sale,
                )
            out[candidate.product_id] = {**quantity.model_dump(mode="json"), "flags": list(flags)}
        return out

    def pick(self, index: int, product_id: str | None, quantity: Quantity | None = None) -> None:
        """picking: the answer for the item `next_pick` showed; None skips it. A `quantity`
        replaces the item's target in the draft (no flag)."""
        with self._lock:
            run = self._require("picking")
            if index != run.pending[0]:
                raise BadRequest(f"índice inesperado {index}; o próximo é {run.pending[0]}")
            decision = run.decisions[index]
            chosen = product_id
            if product_id is None:
                update = {"choice": None, "status": "skipped"}
                quantity = None
            elif product_id in {c.product_id for c in decision.candidates}:
                update = {"choice": product_id, "confidence": None, "status": "user_chosen"}
            else:
                raise BadRequest(f"produto {product_id!r} não está entre os candidatos")
            # Jev's own answer is kept from the first pick, for an item reopened later.
            jev_choice, jev_confidence = run.jev_picks.setdefault(
                index, (decision.choice, decision.confidence)
            )
            run.decisions[index] = decision.model_copy(update=update)
            run.pending.pop(0)
            if quantity is not None:
                run.quantities[index] = quantity
            else:
                run.quantities.pop(index, None)
            with self._db() as db:
                db.save_decisions(run.id, run.decisions)
                db.log(  # Jev's own pick, before the decision is overwritten
                    run.id,
                    "pick",
                    {
                        "index": index,
                        "item": decision.item.name,
                        "jev_choice": jev_choice,
                        "jev_confidence": jev_confidence,
                        "chosen": chosen,
                    },
                )
                if quantity is not None:
                    db.log(
                        run.id,
                        "pick_quantity",
                        {"index": index, "quantity": quantity.model_dump(mode="json")},
                    )
            if not run.pending:
                self._enter_cart_review(run)

    def search_pick(self, index: int, term: str) -> list[Candidate]:
        """picking, the current item: one search with a new term, run by the worker (which owns
        the browser). The item's candidates and search term are replaced; nobody decides, so the
        user picks from the new cards. Waits for the worker; returns the new candidates."""
        term = term.strip()
        if not term:
            raise BadRequest("digite o que buscar")
        done = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            run = self._require("picking")
            if index != run.pending[0]:
                raise BadRequest(f"índice inesperado {index}; o próximo é {run.pending[0]}")
            run.jobs.put(("search", index, term, done, box))
        while not done.wait(0.1):
            with self._lock:  # the run ended or was cancelled: the worker may never answer
                if self._run is not run or self._state != "picking":
                    raise WrongState(self._state)
            if run.worker is None or not run.worker.is_alive():
                raise WrongState(self._state)
        if "error" in box:
            raise box["error"]
        return box["candidates"]

    def reopen(self, index: int) -> None:
        """reviewing_cart -> picking: one item goes back to its cards (the only pending one).
        After it is picked, the run comes back to the summary."""
        with self._lock:
            run = self._require("reviewing_cart")
            if not 0 <= index < len(run.decisions):
                raise BadRequest(f"item {index} não existe")
            if run.decisions[index].status not in ("accepted", "user_chosen", "skipped"):
                raise BadRequest(f"o item {index} ainda não foi decidido")
            run.pending = [index]
            run.total_picks = 1
            run.reopened = True
            with self._db() as db:
                db.log(run.id, "reopen", {"index": index})
            self._set_state("picking")

    def edit_cart(self, edits: list[DraftEdit]) -> CartDraft:
        """reviewing_cart: apply the edits; the draft comes back recomputed."""
        with self._lock:
            run = self._require("reviewing_cart")
            lines = {line.line_id: line for line in run.draft.lines}
            try:
                run.draft = edit_draft(run.draft, edits)
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
            self._remember_edits(run, lines, edits)
            with self._db() as db:
                for edit in edits:
                    quantity = None if edit.quantity is None else edit.quantity.model_dump()
                    db.log(
                        run.id,
                        "cart_edit",
                        {"line_id": edit.line_id, "quantity": quantity, "remove": edit.remove},
                    )
            return run.draft

    @staticmethod
    def _remember_edits(run: _Run, lines: dict, edits: list[DraftEdit]) -> None:
        """Keep the full editor's changes by item, so the summary shows them and a later "trocar"
        doesn't undo them. A quantity is kept only for a line of one item: the quantity of a
        merged line is a sum, and can't be split back."""
        for edit in edits:
            product_id = lines[edit.line_id].line_id
            indexes = [
                i
                for i, d in enumerate(run.decisions)
                if d.status in ("accepted", "user_chosen")
                and d.choice == product_id
                and i not in run.removed
            ]
            if edit.remove:
                run.removed.update(indexes)
            elif edit.quantity is not None and len(indexes) == 1:
                run.quantities[indexes[0]] = edit.quantity

    def _summary(self, run: _Run) -> list[ItemRow]:
        rows, _warnings = item_rows(
            run.decisions, run.prefs, run.histories, run.quantities, run.removed
        )
        return rows

    def report(self) -> dict:
        """The numbers of the current run, as `shopping-minion report` has them."""
        with self._lock:
            if self._run is None:
                raise WrongState(self._state)
            run_id = self._run.id
        with self._db() as db:
            return build_report(db, run_id)

    def confirm_cart(self) -> None:
        """reviewing_cart -> filling_cart: the worker adds the products."""
        with self._lock:
            run = self._require("reviewing_cart")
            if not run.draft.lines:
                raise BadRequest("nada a adicionar ao carrinho")
            self._set_status(run, "adding")
            self._set_state("filling_cart")
            run.jobs.put(("fill", run.draft))

    def retry(self) -> None:
        """done -> filling_cart: the same cart pass on the lines that failed or didn't check.

        The worker still owns the browser in `done`, so it takes the job like the first one.
        The run's draft becomes the retried lines, so the new outcome lines up with it."""
        with self._lock:
            run = self._require("done")
            bad = problem_indexes(run.draft, run.outcome) if run.outcome else []
            if not bad:
                raise BadRequest("nada a tentar de novo")
            lines = [run.draft.lines[i] for i in bad]
            run.draft = CartDraft(lines=lines, skipped=[], estimated_total=_total(lines))
            with self._db() as db:
                db.log(run.id, "retry", {"lines": [line.line_id for line in lines]})
            self._set_status(run, "adding")
            self._set_state("filling_cart")
            run.jobs.put(("fill", run.draft))

    def cancel(self) -> str:
        """Any working or waiting state -> cancelled. In filling_cart it only asks the worker
        to stop: the state changes once the current product is done."""
        with self._lock:
            run = self._run
            if self._state not in WORKING + WAITING or run is None:
                raise WrongState(self._state)
            run.cancel.set()
            if self._state != "filling_cart":
                run.message = "cancelado"
                self._set_status(run, "cancelled")
                self._set_state("cancelled")
                run.jobs.put(("close",))  # a worker waiting for a job closes the browser
            return self._state

    def dispose(self) -> None:
        """done / failed / cancelled -> idle: close the browser and forget the run."""
        with self._lock:
            run = self._require(*FINISHED)
            run.jobs.put(("close",))
            worker = run.worker
        if worker is not None:
            worker.join(JOIN_SECONDS)
        with self._lock:
            if self._run is run:
                self._run = None
                self._set_state("idle")

    def shutdown(self) -> None:
        """Server stopping: close the browser if a run holds it."""
        with self._lock:
            run = self._run
            if run is not None:
                run.cancel.set()
                run.jobs.put(("close",))
        if run is not None and run.worker is not None:
            run.worker.join(JOIN_SECONDS)

    # --- internals, all under the lock ------------------------------------------------------

    @contextlib.contextmanager
    def _db(self) -> Iterator[Storage]:
        # A connection per use: sqlite connections belong to the thread that opened them.
        db = Storage(self._db_path)
        try:
            yield db
        finally:
            db.close()

    def _require(self, *states: str) -> _Run:
        if self._state not in states or self._run is None:
            raise WrongState(self._state)
        return self._run

    def _emit(self, run: _Run, kind: str, data: dict) -> None:
        with self._lock:
            if self._run is not run:  # a thread of a run that is gone
                return
            self._seq += 1
            self._events.append(
                {"seq": self._seq, "state": self._state, "kind": kind, "data": data}
            )
            log.info(
                "run %s %s %s", run.id, kind, json.dumps(data, ensure_ascii=False, default=str)
            )

    def _set_state(self, state: str) -> None:
        self._state = state
        log.info("run %s state %s", self._run.id if self._run else None, state)
        if self._run is not None:
            with self._db() as db:
                db.log(self._run.id, "state", {"state": state})
        self._seq += 1
        self._events.append(
            {"seq": self._seq, "state": state, "kind": "state", "data": {"state": state}}
        )

    def _advance(self, run: _Run, expected: tuple[str, ...], new: str) -> bool:
        """Move to `new` if this run is still current and in one of `expected`."""
        with self._lock:
            if self._run is not run or self._state not in expected:
                return False
            self._set_state(new)
            return True

    def _set_status(self, run: _Run, status: str) -> None:
        with self._db() as db:
            db.set_status(run.id, status)

    def _fail(self, run: _Run, message: str, status: str = "error") -> None:
        with self._lock:
            if self._run is not run or self._state not in WORKING + WAITING:
                return  # cancelled or gone meanwhile
            run.message = message
            # Called from `except` blocks: the traceback goes to the log file too.
            log.error("run %s failed: %s", run.id, message, exc_info=sys.exc_info()[0] is not None)
            self._emit(run, "error", {"message": message})
            self._set_status(run, status)
            self._set_state("failed")

    def _enter_cart_review(self, run: _Run) -> None:
        with self._lock:
            run.reopened = False
            run.draft = draft_cart(
                run.decisions, run.prefs, run.histories, run.quantities, run.removed
            )
            self._set_status(run, "resolved")
            self._set_state("reviewing_cart")

    # --- reading_list: OCR in a plain thread ------------------------------------------------

    def _ocr(self, run: _Run) -> None:
        try:
            items = self._transcribe_fn(run.photos)
        except Exception as exc:
            self._fail(run, f"não consegui ler a lista: {_describe(exc)}")
            return
        with self._lock:
            if self._run is not run or self._state != "reading_list":
                return
            run.ocr_items = list(items)
            run.items = list(items)
            with self._db() as db:
                db.save_items(run.id, run.ocr_items, run.items)
            self._set_status(run, "ocr_done")
            self._set_state("reviewing_list")

    # --- the worker: owns the browser from searching until the run is closed ----------------

    def _work(self, run: _Run) -> None:
        try:
            run.config = load_decide_config(self._config_path)
            with self._db() as db:
                run.prefs = load_preferences(db, self._prefs_path)
            run.history_config = load_history_config(self._history_config_path)
        except Exception as exc:
            self._fail(run, f"configuração inválida: {_describe(exc)}")
            return
        try:
            with self._open_browser_fn() as (_browser, context):
                page = context.new_page()
                try:
                    self._ensure_logged_in_fn(page)
                except NotLoggedInError:
                    self._fail(run, LOGIN_MESSAGE, status="not_logged_in")
                    return
                if not self._sync_history(run, page):
                    return
                if not self._search_and_decide(run, page):
                    return  # the browser closes: there is nothing to look at in it
                while True:  # waiting: the cart pass, or the end of the run
                    job = run.jobs.get()
                    if job[0] == "close":
                        return
                    if job[0] == "fill":
                        self._fill(run, page, job[1])
                    elif job[0] == "search":
                        self._search_pick(run, page, *job[1:])
        except Exception as exc:  # the browser failed to open, or the page died
            self._fail(run, _describe(exc))

    def _sync_history(self, run: _Run, page: Any) -> bool:
        """Read the orders not stored yet (LLD-M4 section 13). A failure doesn't fail the run: it
        goes on with what SQLite has. False when the run was cancelled or is gone."""

        def progress(i: int, n: int, row: Any) -> None:
            if run.cancel.is_set():
                raise _Cancelled

        try:
            with self._db() as db:
                result = self._sync_fn(page, db, run.history_config.first_sync_orders, progress)
            data = {"new": result.new, "skipped": result.skipped, "stored": result.stored}
        except _Cancelled:
            return False
        except Exception as exc:
            data = {"error": _describe(exc)}
        with self._lock:
            if self._run is not run or self._state != "syncing_history":
                return False  # cancelled meanwhile
            self._emit(run, "history", data)
            with self._db() as db:
                db.log(run.id, "history", data)
            self._set_state("searching")
        return True

    def _search_and_decide(self, run: _Run, page: Any) -> bool:
        """True when the run reached a waiting state; False when it ended or was cancelled."""

        def progress(i: int, n: int, item: Item, candidates: list[Candidate]) -> None:
            if run.cancel.is_set():
                raise _Cancelled
            found = {
                "i": i,
                "n": n,
                "term": item.search_term,
                "searched": _searched_terms(candidates),
                "found": len(candidates),
                "retried": bool(getattr(candidates, "retried", False)),
            }
            self._emit(run, "search", found)
            with self._db() as db:  # so an empty search shows in the run's history
                db.log(run.id, "search", found)

        try:
            candidates = self._search_fn(page, run.items, progress)
        except _Cancelled:
            return False
        except Exception as exc:
            self._fail(run, f"a busca falhou: {_describe(exc)}")
            return False
        if not self._advance(run, ("searching",), "deciding"):
            return False
        self._set_status(run, "searched")
        self._emit(run, "decide", {"phase": "start", "accepted": None, "to_pick": None})
        try:
            with self._db() as db:
                run.histories = histories_for(
                    db, run.items, candidates, run.history_config.related_lines
                )
                learned = learned_for(
                    db, run.items, run.config, run.history_config.learn_from_run, run.id
                )
                db.log(run.id, "decide", decide_config_row(run.config))
            decisions = self._decide_fn(
                run.items,
                candidates,
                run.prefs,
                run.config,
                self._client_factory(),
                histories=run.histories,
                learned=learned,
            )
        except Exception as exc:
            self._fail(run, f"o Jev falhou: {_describe(exc)}")
            return False
        with self._lock:
            if self._run is not run or self._state != "deciding":
                return False
            # An item with no results, even after the retry, goes to the picker too: the user
            # can search again with another term, or skip it.
            run.decisions = list(decisions)
            run.pending = [
                i for i, d in enumerate(run.decisions) if d.status in ("ask", "no_match")
            ]
            run.total_picks = len(run.pending)
            with self._db() as db:
                db.save_decisions(run.id, run.decisions)
            self._set_status(run, "decided")
            accepted = sum(d.status == "accepted" for d in run.decisions)
            self._emit(
                run, "decide", {"phase": "end", "accepted": accepted, "to_pick": run.total_picks}
            )
            if run.pending:
                self._set_state("picking")
            else:
                self._enter_cart_review(run)
        return True

    def _search_pick(
        self, run: _Run, page: Any, index: int, term: str, done: threading.Event, box: dict
    ) -> None:
        """One search for the item the picker shows, with the user's new term."""
        try:
            with self._lock:
                if self._run is not run or self._state != "picking" or run.pending[:1] != [index]:
                    raise WrongState(self._state)
                # a term typed by hand replaces the reading, alternatives included
                item = run.decisions[index].item.model_copy(
                    update={"search_term": term, "alternatives": []}
                )
            try:
                found = self._search_one_fn(page, item)
            except Exception as exc:  # the run goes on: the user can try another term
                raise BadRequest(f"a busca falhou: {_describe(exc)}") from exc
            with self._lock:
                if self._run is not run or self._state != "picking" or run.pending[:1] != [index]:
                    raise WrongState(self._state)
                candidates = list(found)
                run.items[index] = item
                run.decisions[index] = Decision(
                    item=item,
                    candidates=candidates,
                    choice=None,
                    confidence=None,
                    status="ask" if candidates else "no_match",
                )
                with self._db() as db:
                    run.histories[index] = histories_for(
                        db, [item], [candidates], run.history_config.related_lines
                    )[0]
                    db.save_decisions(run.id, run.decisions)
                    db.log(
                        run.id,
                        "search",
                        {
                            "i": index + 1,
                            "n": len(run.items),
                            "term": term,
                            "searched": _searched_terms(found),
                            "found": len(candidates),
                            "retried": bool(getattr(found, "retried", False)),
                            "index": index,
                            "picker": True,
                        },
                    )
                box["candidates"] = candidates
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    def _fill(self, run: _Run, page: Any, draft: CartDraft) -> None:
        def progress(i: int, n: int, candidate: Candidate, result: Any) -> None:
            self._emit(
                run,
                "fill",
                {
                    "i": i,
                    "n": n,
                    "name": candidate.name,
                    "status": result.status,
                    "message": result.message,
                },
            )

        try:
            outcome = self._fill_fn(page, draft, progress, should_stop=run.cancel.is_set)
        except Exception as exc:  # the window stays open on whatever the cart holds
            self._fail(run, f"não consegui completar o carrinho: {_describe(exc)}")
            return
        with self._lock:
            if self._run is not run or self._state != "filling_cart":
                return
            run.outcome = outcome
            run.cart_results.update({r.product_id: r for r in outcome.results})
            with self._db() as db:
                db.save_cart(run.id, list(run.cart_results.values()))
                if outcome.after is not None:  # what the report needs for its check line
                    db.log(
                        run.id,
                        "check",
                        {
                            "ok_count": sum(c.ok for c in outcome.checks),
                            "total": len(outcome.checks),
                            "extras": len(outcome.extras),
                            "not_ok": [  # which lines, so a failed check can be looked at
                                {
                                    "product": c.product_name,
                                    "expected": c.expected,
                                    "found": c.found,
                                    "verdict": c.verdict,
                                }
                                for c in outcome.checks
                                if not c.ok
                            ],
                        },
                    )
            if outcome.stopped:
                run.message = "cancelado; o carrinho pode estar incompleto"
                self._set_status(run, "cancelled")
                self._set_state("cancelled")
            else:  # a cancel that came after the last product changes nothing
                self._set_status(run, "done")
                self._set_state("done")
