import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from shopping_minion import orders
from shopping_minion.orders import (
    OrderRow,
    OrdersError,
    order_and_left_out,
    order_from_details,
    rows_from_list,
    select_rows,
    sync_orders,
)
from shopping_minion.storage import Storage

# Everything below is made up, in the shape T12 described.


def list_body(*rows: tuple[str, str, str]) -> dict:
    """rows: (id, createdAt, status), newest first."""
    return {
        "data": {
            "customerViewer": {
                "ordersList": {
                    "rows": [
                        {
                            "id": order_id,
                            "createdAt": created,
                            "deliveryDate": "2026-01-01",
                            "status": status,
                            "total": 100.5,
                            "items": [{"productId": "1", "name": "Qualquer"}],
                        }
                        for order_id, created, status in rows
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": "abc"},
                }
            }
        }
    }


def item(product_id="9001", name="Arroz Exemplo 5kg", quantity=2, unit="UN", price=21.8):
    return {
        "productId": product_id,
        "slug": "arroz-exemplo-5kg",
        "name": name,
        "category": "Mercearia",
        "quantity": quantity,
        "saleUnit": unit,
        "selectedSaleUnit": unit,
        "sellByWeightAndUnit": False,
        "totalPrice": price,
        "productType": "PRODUCT",
    }


def details_body(order_id="7001", items=None, **extra) -> dict:
    order = {
        "id": order_id,
        "items": [item()] if items is None else items,
        "address": {"street": "Rua Inventada, 1"},
        "payment": {"method": "CARD"},
        "changedItemsHistory": [{"type": "CHANGED", "text": "inventado"}],
        **extra,
    }
    return {"data": {"customerViewer": {"order": order}}}


ROW = OrderRow(
    id="7001", placed_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC), status="FINISHED", total=None
)


def test_rows_from_list_keeps_page_order_and_types():
    rows = rows_from_list(
        list_body(
            ("7003", "2026-09-20T15:30:00.000Z", "FINISHED"),
            ("7002", "2026-09-10T09:00:00Z", "PENDING"),
        )
    )
    assert [r.id for r in rows] == ["7003", "7002"]
    assert [r.status for r in rows] == ["FINISHED", "PENDING"]
    assert rows[0].placed_at == datetime(2026, 9, 20, 15, 30, tzinfo=UTC)
    assert rows[0].total == Decimal("100.5")


def test_order_from_details_maps_lines_and_units():
    body = details_body(
        items=[
            item("9001", "Arroz Exemplo 5kg", 2, "UN", 21.8),
            item("9002", "Banana Inventada Kg", 1.34, "KG", 8.04),
        ],
        createdAt="2026-09-20T15:30:00.000Z",
        status="FINISHED",
        total=29.84,
    )
    order = order_from_details(body)
    assert order.order_id == "7001"
    assert order.status == "FINISHED"
    assert order.total == Decimal("29.84")
    assert [(line.product_id, line.quantity, line.unit) for line in order.lines] == [
        ("9001", 2, "un"),
        ("9002", 1.34, "kg"),
    ]
    assert order.lines[1].total_price == Decimal("8.04")
    assert all(line.placed_at == order.placed_at for line in order.lines)
    assert all(line.order_id == "7001" for line in order.lines)


def test_order_header_falls_back_to_the_list_row():
    order = order_from_details(details_body(), ROW)
    assert order.placed_at == ROW.placed_at
    assert order.status == "FINISHED"
    with pytest.raises(ValueError):
        order_from_details(details_body())


def test_lines_with_another_unit_or_no_quantity_are_left_out_and_counted():
    body = details_body(
        items=[
            item("1", unit="UN", quantity=1),
            item("2", unit="CX", quantity=1),
            item("3", unit="UN", quantity=0),
            item("4", unit="KG", quantity=-0.5),
            item("5", unit="KG", quantity=None),
        ]
    )
    order, left_out = order_and_left_out(body, ROW)
    assert [line.product_id for line in order.lines] == ["1"]
    assert left_out == 4


def test_nothing_personal_reaches_the_order():
    dumped = order_from_details(details_body(), ROW).model_dump_json()
    assert "Rua Inventada" not in dumped
    assert "CARD" not in dumped
    assert "inventado" not in dumped


def rows(*specs: tuple[str, str]) -> list[OrderRow]:
    return [
        OrderRow(id=i, placed_at=datetime(2026, 9, 30 - n, tzinfo=UTC), status=s, total=None)
        for n, (i, s) in enumerate(specs)
    ]


def test_first_sync_takes_the_first_n_rows():
    page = rows(*[(str(n), "FINISHED") for n in range(10)])
    chosen, capped = select_rows(page, set(), 4)
    assert [r.id for r in chosen] == ["0", "1", "2", "3"]
    assert not capped


def test_later_sync_takes_the_rows_before_the_first_known():
    page = rows(("5", "FINISHED"), ("4", "FINISHED"), ("3", "FINISHED"), ("2", "FINISHED"))
    chosen, capped = select_rows(page, {"3", "2"}, 10)
    assert [r.id for r in chosen] == ["5", "4"]
    assert not capped
    assert select_rows(page, {"5"}, 10) == ([], False)


def test_more_new_orders_than_the_page_shows_is_flagged():
    page = rows(*[(str(n), "FINISHED") for n in range(10)])
    chosen, capped = select_rows(page, {"old"}, 10)
    assert len(chosen) == 10
    assert capped


def test_first_n_beyond_the_first_page_is_refused(tmp_path):
    storage = Storage(tmp_path / "t.sqlite")
    with pytest.raises(ValueError, match="next page"):
        sync_orders(object(), storage, 11)
    with pytest.raises(ValueError):
        sync_orders(object(), storage, 0)


class FakeRequest:
    def __init__(self, operation):
        self.method = "POST"
        self.post_data = json.dumps({"operationName": operation, "variables": {}})


class FakeResponse:
    def __init__(self, operation, body):
        self.request = FakeRequest(operation)
        self._body = body

    def json(self):
        return self._body


class FakePage:
    """Serves made-up responses on goto, like the page would. It has no way to click or send."""

    def __init__(self, order_list, details):
        self.order_list, self.details = order_list, details
        self.listeners, self.visited = [], []

    def on(self, event, fn):
        assert event == "response"
        self.listeners.append(fn)

    def remove_listener(self, event, fn):
        self.listeners.remove(fn)

    def _emit(self, response):
        for fn in list(self.listeners):
            fn(response)

    def goto(self, url):
        self.visited.append(url)
        if url == orders.ORDERS_URL:
            self._emit(FakeResponse("SomethingElse", {}))
            self._emit(FakeResponse(orders.LIST_OPERATION, self.order_list))
        else:
            order_id = url.rsplit("/", 1)[1]
            # a decoy for another order arrives first; only the opened one counts
            self._emit(FakeResponse(orders.DETAILS_OPERATION, details_body("0000")))
            if order_id in self.details:
                self._emit(FakeResponse(orders.DETAILS_OPERATION, self.details[order_id]))

    def wait_for_timeout(self, _ms):
        pass


def make_world(*specs):
    """specs: (id, createdAt, status). Every order's details have one line."""
    details = {i: details_body(i, items=[item(product_id=f"p{i}")]) for i, _, _ in specs}
    return FakePage(list_body(*specs), details)


@pytest.fixture
def storage(tmp_path):
    store = Storage(tmp_path / "t.sqlite")
    yield store
    store.close()


SPECS = [
    ("7005", "2026-09-25T10:00:00Z", "FINISHED"),
    ("7004", "2026-09-20T10:00:00Z", "PENDING"),
    ("7003", "2026-09-15T10:00:00Z", "FINISHED"),
    ("7002", "2026-09-10T10:00:00Z", "FINISHED"),
    ("7001", "2026-09-05T10:00:00Z", "FINISHED"),
]


def test_first_sync_reads_the_first_n_rows_and_skips_unfinished(storage):
    page = make_world(*SPECS)
    seen = []
    result = sync_orders(page, storage, 3, progress=lambda i, n, row: seen.append((i, n, row.id)))
    assert (result.new, result.skipped, result.stored) == (2, 1, 2)
    assert storage.known_order_ids() == {"7005", "7003"}
    assert seen == [(1, 3, "7005"), (2, 3, "7004"), (3, 3, "7003")]
    assert page.listeners == []  # the listeners are removed afterwards
    # only pages were opened, and the unfinished order was not opened at all
    assert page.visited == [orders.ORDERS_URL, f"{orders.ORDERS_URL}/7005"] + [
        f"{orders.ORDERS_URL}/7003"
    ]


def test_second_sync_reads_only_what_is_new(storage):
    sync_orders(make_world(*SPECS[2:]), storage, 10)
    assert storage.known_order_ids() == {"7003", "7002", "7001"}
    page = make_world(*SPECS)
    result = sync_orders(page, storage, 10)
    assert (result.new, result.skipped, result.stored) == (1, 1, 4)
    assert not result.capped
    assert page.visited == [orders.ORDERS_URL, f"{orders.ORDERS_URL}/7005"]


def test_nothing_new_opens_only_the_list(storage):
    sync_orders(make_world(*SPECS), storage, 10)
    page = make_world(*SPECS)
    result = sync_orders(page, storage, 10)
    assert (result.new, result.skipped, result.stored) == (0, 0, 4)
    assert page.visited == [orders.ORDERS_URL]


def test_left_out_lines_are_counted_in_the_result(storage):
    page = make_world(SPECS[0])
    page.details["7005"] = details_body("7005", items=[item("1"), item("2", unit="CX")])
    result = sync_orders(page, storage, 10)
    assert result.left_out_lines == 1
    assert len(storage.order_lines()) == 1


def test_a_missing_response_is_an_error_after_the_wait(storage, monkeypatch):
    monkeypatch.setattr(orders, "WAIT_SECONDS", 0.0)
    page = make_world(SPECS[0])
    page.details.clear()  # only the decoy for another order arrives
    with pytest.raises(OrdersError, match="details of order 7005"):
        sync_orders(page, storage, 10)
    assert storage.known_order_ids() == set()


def test_response_matchers_read_the_operation_the_page_sent():
    assert orders.is_list_response(FakeResponse(orders.LIST_OPERATION, {}))
    assert not orders.is_list_response(FakeResponse(orders.DETAILS_OPERATION, {}))
    assert orders.is_details_response(FakeResponse(orders.DETAILS_OPERATION, {}))
    no_body = FakeResponse(orders.LIST_OPERATION, {})
    no_body.request.post_data = None
    assert not orders.is_list_response(no_body)
    get = FakeResponse(orders.LIST_OPERATION, {})
    get.request.method = "GET"
    assert not orders.is_list_response(get)


@pytest.mark.live
def test_live_sync_into_a_temporary_database(tmp_path):
    from shopping_minion.browser import ensure_logged_in, open_browser

    storage = Storage(tmp_path / "live.sqlite")
    with open_browser() as (_browser, context):
        page = context.new_page()
        ensure_logged_in(page)
        result = sync_orders(page, storage, 3)
    print(f"new={result.new} skipped={result.skipped} stored={result.stored}")
    assert result.stored == result.new == len(storage.known_order_ids())
    assert result.new + result.skipped <= 3
    assert all(line.quantity > 0 for line in storage.order_lines())
    storage.close()
