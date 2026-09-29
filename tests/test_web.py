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
