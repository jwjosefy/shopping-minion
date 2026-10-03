"""The HTTP side of the web app: routes, SSE, static files and the access token (LLD-M2 5).

All the logic is in `RunStateMachine`; this module turns its answers into the JSON of the
LLD and keeps strangers on the Wi-Fi out. Decimals go out as JSON numbers (floats).
"""

import asyncio
import io
import ipaddress
import json
import secrets
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shopping_minion.decide import describe_candidate
from shopping_minion.items import Candidate, CartDraft, Item
from shopping_minion.reconcile import expected_amount, format_amount, normalize
from shopping_minion.web.statemachine import BadRequest, RunStateMachine, WrongState
from shopping_minion.workflow import CartOutcome, DraftEdit, problem_indexes, problem_message

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "sm_token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


# --- access (LLD-M2 5.1) ----------------------------------------------------------------------


def is_loopback(host: str | None) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:  # a name like "testclient", or no client at all
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


@dataclass
class Access:
    """Who may call the API. Loopback always may. Others need the token, if there is one;
    with `token=None` (`--local`) nobody else is let in."""

    token: str | None = None
    url: str | None = None  # the LAN URL with the token, for /api/access and the QR
    is_loopback: Callable[[str | None], bool] = field(default=is_loopback)


def lan_ip() -> str | None:
    """The address of the interface that holds the default route.

    A UDP socket "connected" to a private address makes the kernel pick the outgoing
    interface without sending a packet; its own address is then the one the phone reaches.
    `gethostbyname(gethostname())` is the fallback, but on many machines it answers 127.0.1.1,
    or the address of a docker or VPN interface, so it is only used when the first fails.
    None when neither gives a non-loopback address.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            candidate = probe.getsockname()[0]
    except OSError:
        candidate = ""
    if not candidate or is_loopback(candidate):
        try:
            candidate = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    return None if is_loopback(candidate) else candidate


def make_access(*, local: bool, port: int, ip: str | None = None) -> Access | None:
    """`--local`: no token, no URL. Otherwise a random token and the LAN URL; None when
    there is no LAN address to put in it."""
    if local:
        return Access()
    ip = ip or lan_ip()
    if ip is None:
        return None
    token = secrets.token_urlsafe(32)
    return Access(token=token, url=f"http://{ip}:{port}/?t={token}")


def qr_svg(url: str) -> str:
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def print_qr(url: str, out=None) -> None:
    code = qrcode.QRCode(border=2)
    code.add_data(url)
    code.print_ascii(out=out, invert=True)


# --- JSON shapes (LLD-M2 5) -------------------------------------------------------------------


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def candidate_json(candidate: Candidate) -> dict:
    data = candidate.model_dump(mode="json")
    data["price"] = _num(candidate.price)
    data["list_price"] = _num(candidate.list_price)
    return data


def cart_draft_json(draft: CartDraft) -> dict:
    return {
        "lines": [
            {
                "line_id": line.line_id,
                "product": candidate_json(line.candidate),
                "items": [item.name for item in line.items],
                "quantity": line.quantity.model_dump(mode="json"),
                "clicks": line.target.clicks,
                "flags": list(line.flags),
                "estimated_price": _num(line.estimated_price),
            }
            for line in draft.lines
        ],
        "skipped": [decision.item.name for decision in draft.skipped],
        "estimated_total": _num(draft.estimated_total),
        "warnings": list(draft.warnings),  # beyond the LLD: items left out with a reason
    }


def outcome_json(outcome: CartOutcome, draft: CartDraft | None) -> dict:
    """`checks[i]` is the check of `draft.lines[i]`, which is where the item names come from."""
    lines = draft.lines if draft is not None else []
    checks = []
    for i, check in enumerate(outcome.checks):
        names = [item.name for item in lines[i].items] if i < len(lines) else [check.item_name]
        checks.append(
            {
                "item_names": names,
                "product_name": check.product_name,
                "expected": check.expected,
                "found": check.found,
                "was_before": check.was_before,
                "ok": check.ok,
                "verdict": check.verdict,
            }
        )
    before = {normalize(name) for name, _ in outcome.before or []}
    problems = []
    for i in problem_indexes(draft, outcome) if draft is not None else []:
        line = draft.lines[i]
        result = outcome.results[i] if i < len(outcome.results) else None
        check = outcome.checks[i] if i < len(outcome.checks) else None
        found = check.found if check is not None else None
        problems.append(
            {
                "line_id": line.line_id,
                "item_names": [item.name for item in line.items],
                "product_name": line.candidate.name,
                "expected": check.expected
                if check is not None
                else format_amount(expected_amount(line.candidate, line.target)),
                "found": found
                if found is not None
                else (result.quantity_shown if result else None),
                "message": problem_message(result, check) or "não foi adicionado",
            }
        )
    return {
        "checks": checks,
        "problems": problems,  # every line that isn't ok, with why; what "tentar de novo" retries
        "extras": [
            {"name": name, "quantity": quantity, "was_before": normalize(name) in before}
            for name, quantity in outcome.extras
        ],
        "ok_count": sum(check.ok for check in outcome.checks),
        "total": len(outcome.checks),
        "stopped": outcome.stopped,
        # beyond the LLD: what each add reported, and why a cart read may be missing
        "results": [
            {
                "product_name": lines[i].candidate.name if i < len(lines) else result.product_id,
                "status": result.status,
                "message": result.message,
            }
            for i, result in enumerate(outcome.results)
        ],
        "before_error": outcome.before_error,
        "after_error": outcome.after_error,
    }


def snapshot_json(machine: RunStateMachine) -> dict:
    snap = machine.snapshot()
    data: dict = {"state": snap.state, "run_id": snap.run_id, "message": snap.message}
    if snap.items is not None:
        data["list"] = [item.model_dump(mode="json") for item in snap.items]
    if snap.picks_left is not None:
        data["picks_left"] = snap.picks_left
    if snap.draft is not None:
        data["cart_draft"] = cart_draft_json(snap.draft)
    if snap.outcome is not None:
        data["outcome"] = outcome_json(snap.outcome, snap.draft)
    return data


# --- request bodies ---------------------------------------------------------------------------


class ListBody(BaseModel):
    items: list[Item]


class PickBody(BaseModel):
    index: int
    product_id: str | None = None


class CartDraftBody(BaseModel):
    lines: list[DraftEdit]


# --- server-sent events -----------------------------------------------------------------------


async def sse_stream(
    machine: RunStateMachine,
    after: int = 0,
    *,
    follow: bool = True,
    poll: float = 0.2,
    keepalive: float = 15.0,
) -> AsyncIterator[str]:
    """`data: <event json>` for every event after `after`, then new ones as they arrive.

    With `follow=False` it stops after the events that already exist. A cursor beyond the last
    event (the server restarted) starts over from the beginning of the log.
    """
    if after > machine.last_seq:
        after = 0
    quiet = 0.0
    while True:
        events = machine.events_after(after)
        for event in events:
            after = event["seq"]
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if not follow:
            return
        quiet = 0.0 if events else quiet + poll
        if quiet >= keepalive:
            yield ": keepalive\n\n"
            quiet = 0.0
        await asyncio.sleep(poll)


# --- the app ----------------------------------------------------------------------------------


def create_app(machine: RunStateMachine, *, access: Access) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        machine.shutdown()  # the browser must not outlive the server

    app = FastAPI(title="shopping-minion", lifespan=lifespan)
    qr = qr_svg(access.url) if access.url else None

    @app.exception_handler(WrongState)
    async def _wrong_state(_request: Request, exc: WrongState):
        return JSONResponse({"state": exc.state}, status_code=409)

    @app.exception_handler(BadRequest)
    async def _bad_request(_request: Request, exc: BadRequest):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.client.host if request.client else None
        # A page on another site can make the browser send a form to this machine; its
        # Origin gives it away.
        origin = request.headers.get("origin")
        if (
            request.method not in SAFE_METHODS
            and origin
            and urlsplit(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse({"detail": "origem não permitida"}, status_code=403)
        if access.is_loopback(host):
            return await call_next(request)
        token = access.token
        if token is None:
            return JSONResponse({"detail": "somente nesta máquina"}, status_code=401)
        given = request.query_params.get("t")
        if given is not None and secrets.compare_digest(given, token):
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "t"]
            target = request.url.path + (f"?{urlencode(rest)}" if rest else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
            return response
        if secrets.compare_digest(request.cookies.get(COOKIE, ""), token):
            return await call_next(request)
        return JSONResponse({"detail": "token necessário"}, status_code=401)

    @app.get("/api/access")
    def get_access(request: Request):
        host = request.client.host if request.client else None
        if not access.is_loopback(host):
            return JSONResponse({"detail": "somente nesta máquina"}, status_code=403)
        return {"url": access.url, "qr_svg": qr}

    @app.get("/api/run")
    def get_run():
        return snapshot_json(machine)

    @app.get("/api/run/events")
    def get_events(after: int = 0, follow: bool = True):
        return StreamingResponse(
            sse_stream(machine, after, follow=follow),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/run/photo")
    def get_photo():  # beyond the LLD: the review screen shows the photo beside the list
        photo = machine.photo_path()
        if photo is None or not photo.exists():
            return JSONResponse({"detail": "sem foto"}, status_code=404)
        return FileResponse(photo)

    @app.post("/api/run", status_code=202)
    async def post_run(photo: UploadFile):
        data = await photo.read()
        if not data:
            raise BadRequest("arquivo vazio")
        run_id = machine.start(data, Path(photo.filename or "").suffix)
        return {"run_id": run_id}

    @app.put("/api/run/list")
    def put_list(body: ListBody):
        machine.save_list(body.items)
        return {"items": [item.model_dump(mode="json") for item in body.items]}

    @app.post("/api/run/list/confirm", status_code=202)
    def post_list_confirm():
        machine.confirm_list()
        return {"state": machine.state}

    @app.get("/api/run/picks/next")
    def get_next_pick():
        pick = machine.next_pick()
        return {
            "index": pick.index,
            "left": pick.left,
            "position": pick.position,  # beyond the LLD: "3 de 28" needs both
            "total": pick.total,
            "item": pick.decision.item.model_dump(mode="json"),
            "candidates": [
                {**candidate_json(c), "description": describe_candidate(c)} for c in pick.candidates
            ],
            "jev": {
                "choice": pick.decision.choice,
                "confidence": pick.decision.confidence,
                "nothing_fit": pick.nothing_fit,
            },
            "no_results": pick.no_results,
        }

    @app.post("/api/run/picks")
    def post_pick(body: PickBody):
        machine.pick(body.index, body.product_id)
        return {"state": machine.state}

    @app.put("/api/run/cart-draft")
    def put_cart_draft(body: CartDraftBody):
        return {"cart_draft": cart_draft_json(machine.edit_cart(body.lines))}

    @app.post("/api/run/cart-draft/confirm", status_code=202)
    def post_cart_confirm():
        machine.confirm_cart()
        return {"state": machine.state}

    @app.post("/api/run/retry", status_code=202)
    def post_retry():
        machine.retry()
        return {"state": machine.state}

    @app.post("/api/run/cancel")
    def post_cancel():
        return {"state": machine.cancel()}

    @app.delete("/api/run")
    def delete_run():
        machine.dispose()
        return {"state": machine.state}

    @app.get("/api/runs")
    def get_runs():
        return machine.recent_runs()

    if STATIC_DIR.is_dir():

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(STATIC_DIR / "index.html")

        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
