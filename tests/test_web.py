import re

import pytest
from fastapi.testclient import TestClient

from shopping_minion.contracts import ConfirmedList, TranscribedItem, TranscribedList
from shopping_minion.runs import RunStore
from shopping_minion.web.app import create_app

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeIntake:
    def __init__(self, result=None, error=None):
        self.result = result or TranscribedList(
            items=[
                TranscribedItem(name="atum", source_line="Atum"),
                TranscribedItem(
                    name="presunto", quantity=600, unit="g", source_line="Presunto 600 g"
                ),
                TranscribedItem(
                    name="lanches das crianças",
                    needs_clarification=True,
                    source_line="Lanches das crianças",
                ),
            ]
        )
        self.error = error

    def transcribe(self, image, media_type):
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "runs")


def client_for(store, intake):
    return TestClient(create_app(store, intake_factory=lambda: intake))


def upload(client, content=JPEG, media_type="image/jpeg", htmx=False):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(
        "/runs",
        files={"photo": ("list.jpg", content, media_type)},
        headers=headers,
        follow_redirects=False,
    )


def test_upload_page_renders(store):
    assert "Ler lista" in client_for(store, FakeIntake()).get("/").text


def test_upload_saves_photo_and_transcription_then_redirects_to_review(store):
    response = upload(client_for(store, FakeIntake()))
    assert response.status_code == 303
    run_id = re.match(r"/runs/(.+)/review", response.headers["location"])[1]
    assert store.photo(run_id).read_bytes() == JPEG
    assert len(store.load(run_id, "transcription", TranscribedList).items) == 3


def test_htmx_upload_uses_hx_redirect(store):
    response = upload(client_for(store, FakeIntake()), htmx=True)
    assert response.headers["HX-Redirect"].endswith("/review")


def test_upload_rejects_unsupported_image(store):
    response = upload(client_for(store, FakeIntake()), media_type="image/heic")
    assert "unsupported image type" in response.text
    assert not list((store.base).glob("*")) if store.base.exists() else True


def test_intake_failure_is_shown_not_raised(store):
    response = upload(client_for(store, FakeIntake(error=RuntimeError("boom"))))
    assert response.status_code == 200
    assert "boom" in response.text


def test_review_shows_items_with_source_lines(store):
    client = client_for(store, FakeIntake())
    review = client.get(upload(client).headers["location"])
    assert review.status_code == 200
    assert 'value="presunto"' in review.text
    assert "“Presunto 600 g”" in review.text
    assert 'value="600"' in review.text


def test_confirm_saves_edited_list_in_form_order(store):
    client = client_for(store, FakeIntake())
    run_id = upload(client).headers["location"].split("/")[2]
    response = client.post(
        f"/runs/{run_id}/confirm",
        data={
            "items-b-name": "papel higiênico",
            "items-b-constraints": "folha dupla, 12 rolos",
            "items-a-name": "atum",
            "items-a-quantity": "2",
            "items-a-unit": "un",
            "items-c-name": "",  # deleted content: dropped
            "items-d-name": "lanches",
            "items-d-clarify": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    confirmed = store.load(run_id, "confirmed", ConfirmedList)
    assert [i.name for i in confirmed.items] == ["papel higiênico", "atum", "lanches"]
    assert confirmed.items[0].constraints == ["folha dupla", "12 rolos"]
    assert confirmed.items[1].quantity == 2
    assert confirmed.items[2].needs_clarification
    # the transcription is kept next to the confirmed list (ADR-0002: the diff is eval data)
    assert store.load(run_id, "transcription", TranscribedList) is not None


def test_confirm_rejects_invalid_quantity(store):
    client = client_for(store, FakeIntake())
    run_id = upload(client).headers["location"].split("/")[2]
    response = client.post(
        f"/runs/{run_id}/confirm", data={"items-a-name": "atum", "items-a-quantity": "dois"}
    )
    assert response.status_code == 422


def test_unknown_or_malformed_run_is_404(store):
    client = client_for(store, FakeIntake())
    assert client.get("/runs/20260101-000000-abcdef/review").status_code == 404
    assert client.get("/runs/..%2F..%2Fetc/photo").status_code == 404


def test_add_row_returns_an_empty_row(store):
    client = client_for(store, FakeIntake())
    run_id = upload(client).headers["location"].split("/")[2]
    row = client.get(f"/runs/{run_id}/row")
    assert "<tr" in row.text and 'name="items-' in row.text


# --- run + report ---------------------------------------------------------------------------

import time
from contextlib import asynccontextmanager
from decimal import Decimal

from shopping_minion.contracts import Candidate, CartLine, UnitOfSale
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import ProductChoice
from shopping_minion.services import Services


class _Catalog:
    async def search(self, query):
        return [
            Candidate(
                id="1",
                name=f"{query.title()} Premium",
                brand="Marca",
                unit_of_sale=UnitOfSale(kind="unit"),
                price=Decimal("9.5"),
                url="https://s/1",
            )
        ]


class _Backend:
    name = "stub"

    def choose(self, item, candidates, preference):
        return ProductChoice("1", 0.9, rationale="matches the request")


class _Executor:
    async def add_to_cart(self, candidate, sale):
        return CartLine(product_id=candidate.id, quantity=sale.steps_or_units, verified=False)


@asynccontextmanager
async def _services():
    yield Services(_Catalog(), _Backend(), _Executor(), Preferences({}), dry_run=True)


@asynccontextmanager
async def _broken_services():
    raise RuntimeError("profile missing")
    yield


def _confirmed_run(client, names=("atum", "papel")):
    run_id = upload(client).headers["location"].split("/")[2]
    data = {f"items-{i}-name": n for i, n in enumerate(names)}
    client.post(f"/runs/{run_id}/confirm", data=data, follow_redirects=False)
    return run_id


def _wait_until_finished(client, run_id):
    for _ in range(50):
        response = client.get(f"/runs/{run_id}/progress")
        if response.status_code == 286:  # htmx: stop polling
            return response
        time.sleep(0.1)
    raise AssertionError("run did not finish")


# `with TestClient(...)` keeps one event loop alive, so the background run isn't cancelled
# when the request that started it returns (uvicorn behaves like that too).


def test_confirmed_page_offers_to_start(store):
    with TestClient(create_app(store, lambda: FakeIntake(), _services)) as client:
        run_id = _confirmed_run(client)
        assert f'action="/runs/{run_id}/start"' in client.get(f"/runs/{run_id}").text


def test_start_requires_a_confirmed_list(store):
    with TestClient(create_app(store, lambda: FakeIntake(), _services)) as client:
        run_id = upload(client).headers["location"].split("/")[2]
        assert client.post(f"/runs/{run_id}/start", follow_redirects=False).status_code == 409


def test_run_finishes_and_report_shows_products_and_dry_run_notice(store):
    with TestClient(create_app(store, lambda: FakeIntake(), _services)) as client:
        run_id = _confirmed_run(client)
        assert client.post(f"/runs/{run_id}/start", follow_redirects=False).status_code == 303
        response = _wait_until_finished(client, run_id)

    assert "Atum Premium" in response.text and "Papel Premium" in response.text
    assert "Simulação" in response.text and "90%" in response.text
    assert "quantidade assumida" in response.text
    assert "R$ 19.00" in response.text


def test_failed_run_shows_the_error_instead_of_spinning(store):
    with TestClient(create_app(store, lambda: FakeIntake(), _broken_services)) as client:
        run_id = _confirmed_run(client)
        client.post(f"/runs/{run_id}/start", follow_redirects=False)
        response = _wait_until_finished(client, run_id)
    assert "profile missing" in response.text
