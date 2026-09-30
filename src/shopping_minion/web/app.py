"""Web app: upload -> intake -> review (HLD §4.1, ADR-0002)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from secrets import token_hex

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from shopping_minion.config import load_models_config
from shopping_minion.contracts import ConfirmedItem, ConfirmedList, RunReport, TranscribedList
from shopping_minion.intake import Intake, build_intake, validate_image
from shopping_minion.runs import RunStore
from shopping_minion.services import Services, ServicesFactory, default_services
from shopping_minion.workflow import run_list

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
TEMPLATES.env.filters["fmt_qty"] = lambda q: "" if q is None else f"{q:g}"

_FIELD = re.compile(
    r"^items-(?P<key>[0-9a-z]+)-(?P<field>name|quantity|unit|constraints|clarify|source)$"
)


def _default_intake() -> Intake:
    return build_intake(load_models_config().intake)


class RunState:
    """In-memory progress of a run that is executing (the report file is the durable record)."""

    def __init__(self, total: int) -> None:
        self.done, self.total = 0, total
        self.current: str | None = None
        self.finished = False
        self.error: str | None = None


def create_app(
    store: RunStore | None = None,
    intake_factory: Callable[[], Intake] = _default_intake,
    services_factory: ServicesFactory = default_services,
) -> FastAPI:
    store = store or RunStore()
    running: dict[str, RunState] = {}
    tasks: set[asyncio.Task[None]] = set()
    get_intake = cache(intake_factory)  # built on first upload, so the app starts without keys
    app = FastAPI(title="Shopping Minion")

    def run_or_404(run_id: str) -> str:
        if not store.exists(run_id):
            raise HTTPException(404, "run not found")
        return run_id

    @app.get("/", response_class=HTMLResponse)
    def upload_page(request: Request) -> Response:
        return TEMPLATES.TemplateResponse(request, "upload.html")

    @app.post("/runs")
    def create_run(request: Request, photo: UploadFile) -> Response:
        image = photo.file.read()
        media_type = photo.content_type or ""
        try:
            validate_image(image, media_type)
        except ValueError as e:
            return TEMPLATES.TemplateResponse(request, "_error.html", {"message": str(e)})

        run_id = store.create()
        store.save_photo(run_id, image, media_type)
        try:
            transcription = get_intake().transcribe(image, media_type)
        except Exception as e:  # noqa: BLE001 - any provider error is shown; the photo stays saved
            message = f"Não consegui ler a lista ({type(e).__name__}): {e}"
            return TEMPLATES.TemplateResponse(request, "_error.html", {"message": message})
        store.save(run_id, "transcription", transcription)

        target = f"/runs/{run_id}/review"
        if request.headers.get("HX-Request"):
            return Response(headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    @app.get("/runs/{run_id}/photo")
    def run_photo(run_id: str) -> FileResponse:
        photo = store.photo(run_or_404(run_id))
        if photo is None:
            raise HTTPException(404, "no photo")
        return FileResponse(photo)

    @app.get("/runs/{run_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, run_id: str) -> Response:
        run_or_404(run_id)
        items = store.load(run_id, "confirmed", ConfirmedList) or store.load(
            run_id, "transcription", TranscribedList
        )
        if items is None:
            raise HTTPException(404, "run has no transcription")
        rows = [(token_hex(4), item) for item in items.items]
        return TEMPLATES.TemplateResponse(request, "review.html", {"run_id": run_id, "rows": rows})

    @app.get("/runs/{run_id}/row", response_class=HTMLResponse)
    def empty_row(request: Request, run_id: str) -> Response:
        run_or_404(run_id)
        return TEMPLATES.TemplateResponse(request, "_row.html", {"key": token_hex(4), "item": None})

    @app.post("/runs/{run_id}/confirm")
    async def confirm(request: Request, run_id: str) -> Response:
        run_or_404(run_id)
        try:
            confirmed = parse_review_form(await request.form())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        store.save(run_id, "confirmed", confirmed)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str) -> Response:
        confirmed = store.load(run_or_404(run_id), "confirmed", ConfirmedList)
        if confirmed is None:
            return RedirectResponse(f"/runs/{run_id}/review", status_code=303)
        return TEMPLATES.TemplateResponse(
            request, "confirmed.html", {"run_id": run_id, "confirmed": confirmed}
        )

    async def execute_run(run_id: str, confirmed: ConfirmedList, state: RunState) -> None:
        async def on_progress(done: int, total: int, item: object) -> None:
            state.done = done

        try:
            async with services_factory() as services:
                services_: Services = services
                await run_list(
                    confirmed,
                    services_.catalog,
                    services_.backend,
                    services_.executor,
                    services_.preferences,
                    store,
                    run_id,
                    on_progress=on_progress,
                    dry_run=services_.dry_run,
                )
        except Exception as e:  # noqa: BLE001 - shown on the run page instead of a dead spinner
            state.error = f"{type(e).__name__}: {e}"
        finally:
            state.finished = True

    @app.post("/runs/{run_id}/start")
    async def start_run(run_id: str) -> Response:
        confirmed = store.load(run_or_404(run_id), "confirmed", ConfirmedList)
        if confirmed is None:
            raise HTTPException(409, "confirm the list first")
        if run_id not in running or running[run_id].finished:
            state = running[run_id] = RunState(total=len(confirmed.items))
            task = asyncio.create_task(execute_run(run_id, confirmed, state))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return RedirectResponse(f"/runs/{run_id}/report", status_code=303)

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report_page(request: Request, run_id: str) -> Response:
        run_or_404(run_id)
        return TEMPLATES.TemplateResponse(request, "report.html", {"run_id": run_id})

    @app.get("/runs/{run_id}/progress", response_class=HTMLResponse)
    def progress(request: Request, run_id: str) -> Response:
        run_or_404(run_id)
        state = running.get(run_id)
        report = store.load(run_id, "report", RunReport)
        context = {
            "run_id": run_id,
            "state": state,
            "report": report,
            "finished": state is None or state.finished,
        }
        # htmx stops polling on HTTP 286
        return TEMPLATES.TemplateResponse(
            request, "_progress.html", context, status_code=286 if context["finished"] else 200
        )

    return app


def parse_review_form(form: FormData) -> ConfirmedList:
    """Rows keep the order they appear in the form; rows with an empty name are dropped."""
    rows: dict[str, dict[str, str]] = {}
    for field_name, value in form.multi_items():
        match = _FIELD.match(field_name)
        if match and isinstance(value, str):
            rows.setdefault(match["key"], {})[match["field"]] = value.strip()

    items = []
    for row in rows.values():
        if not row.get("name"):
            continue
        quantity_text = row.get("quantity", "").replace(",", ".")
        try:
            quantity = float(quantity_text) if quantity_text else None
        except ValueError as e:
            raise ValueError(f"quantidade inválida para {row['name']!r}: {quantity_text!r}") from e
        items.append(
            ConfirmedItem(
                name=row["name"],
                quantity=quantity,
                unit=(row.get("unit") or None) if quantity else None,
                constraints=[c.strip() for c in row.get("constraints", "").split(",") if c.strip()],
                needs_clarification="clarify" in row,
                source_line=row.get("source") or None,
            )
        )
    return ConfirmedList(items=items)
