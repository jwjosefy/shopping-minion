"""Order history sync (LLD-M4 sections 8 to 10).

Read-only, through the store's own pages. It opens the order list and each new order the way a
user would, and reads the JSON those pages receive. It clicks nothing and sends nothing by
hand. The delivery address, the payment method and the status history are in the same
responses and are never read.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from playwright.sync_api import Page, Response
from pydantic import Field

from shopping_minion.browser import BASE_URL, settle
from shopping_minion.items import Contract

if TYPE_CHECKING:  # storage imports the contracts from here
    from shopping_minion.storage import Storage

LIST_OPERATION = "CustomerOrdersListPaginated"
DETAILS_OPERATION = "OrderDetailsQuery"
WAIT_SECONDS = 20.0
FINISHED = "FINISHED"
# The list's first page has 10 orders (T12). Its next page was never observed.
MAX_ORDERS = 10
ORDERS_URL = f"{BASE_URL}/minha-conta/pedidos"
_UNITS: dict[str, Literal["un", "kg"]] = {"UN": "un", "KG": "kg"}


class OrdersError(RuntimeError):
    """The page did not deliver its order data in time."""


class OrderLine(Contract):
    order_id: str
    placed_at: datetime  # the order's createdAt, UTC
    product_id: str  # the search's product id (T12)
    name: str
    quantity: float = Field(gt=0)
    unit: Literal["un", "kg"]  # from selectedSaleUnit: "UN" -> un, "KG" -> kg
    total_price: Decimal | None


class Order(Contract):
    order_id: str
    placed_at: datetime
    status: str  # only FINISHED orders are stored
    total: Decimal | None
    lines: list[OrderLine]


class OrderRow(Contract):
    """One row of the order list: enough to decide whether to open the order."""

    id: str
    placed_at: datetime
    status: str
    total: Decimal | None


@dataclass
class SyncResult:
    new: int  # orders read and saved now
    skipped: int  # rows that weren't FINISHED
    stored: int  # orders in the database afterwards
    left_out_lines: int = 0  # lines dropped: unit other than UN/KG, or quantity not > 0
    capped: bool = False  # more orders were new than the list's first page shows


def _money(value: float | int | str | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _utc(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def rows_from_list(body: dict) -> list[OrderRow]:
    """`data.customerViewer.ordersList.rows`, in page order (newest first)."""
    rows = body["data"]["customerViewer"]["ordersList"]["rows"]
    return [
        OrderRow(
            id=str(row["id"]),
            placed_at=_utc(row["createdAt"]),
            status=row["status"],
            total=_money(row.get("total")),
        )
        for row in rows
    ]


def order_and_left_out(body: dict, row: OrderRow | None = None) -> tuple[Order, int]:
    """`data.customerViewer.order` and its `items`, plus how many lines were left out.

    A line whose `selectedSaleUnit` isn't UN or KG, or whose `quantity` isn't > 0, is left
    out. The order's date, status and total come from the details when they have them, and
    from the list's row otherwise (T12 observed them on the list rows only; inference).
    """
    order = body["data"]["customerViewer"]["order"]
    order_id = str(order["id"])
    created = order.get("createdAt")
    placed_at = _utc(created) if created else (row.placed_at if row else None)
    status = order.get("status") or (row.status if row else None)
    if placed_at is None or status is None:
        raise ValueError("the order has no date or status, and no list row was given")
    total = _money(order["total"]) if "total" in order else (row.total if row else None)
    lines, left_out = [], 0
    for item in order.get("items") or []:
        unit = _UNITS.get(item.get("selectedSaleUnit"))
        quantity = item.get("quantity")
        if unit is None or not isinstance(quantity, int | float) or not quantity > 0:
            left_out += 1
            continue
        lines.append(
            OrderLine(
                order_id=order_id,
                placed_at=placed_at,
                product_id=str(item["productId"]),
                name=item["name"],
                quantity=quantity,
                unit=unit,
                total_price=_money(item.get("totalPrice")),
            )
        )
    return (
        Order(order_id=order_id, placed_at=placed_at, status=status, total=total, lines=lines),
        left_out,
    )


def order_from_details(body: dict, row: OrderRow | None = None) -> Order:
    return order_and_left_out(body, row)[0]


def _operation(response: Response) -> str | None:
    """The operationName of what the page sent for this response. Read, never built."""
    sent = response.request
    if sent.method != "POST":
        return None
    try:
        return json.loads(sent.post_data or "").get("operationName")
    except (ValueError, AttributeError):
        return None


def is_list_response(response: Response) -> bool:
    return _operation(response) == LIST_OPERATION


def is_details_response(response: Response) -> bool:
    return _operation(response) == DETAILS_OPERATION


def _details_id(body: dict) -> str | None:
    try:
        return str(body["data"]["customerViewer"]["order"]["id"])
    except (KeyError, TypeError):
        return None


class _Listener:
    """Keeps the responses the page receives, so the code can look at them as they arrive."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.seen: list[Response] = []
        self._read = 0

    def __enter__(self) -> "_Listener":
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc: object) -> None:
        self.page.remove_listener("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        self.seen.append(response)

    def wait_for(self, matches: Callable[[Response], dict | None], what: str) -> dict:
        """The first body `matches` accepts, from the responses seen so far or arriving."""
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            while self._read < len(self.seen):
                found = matches(self.seen[self._read])
                self._read += 1
                if found is not None:
                    return found
            if time.monotonic() >= deadline:
                raise OrdersError(f"no {what} within {WAIT_SECONDS:.0f} s")
            self.page.wait_for_timeout(100)


def _list_body(response: Response) -> dict | None:
    return response.json() if is_list_response(response) else None


def _details_body_of(order_id: str) -> Callable[[Response], dict | None]:
    def matches(response: Response) -> dict | None:
        if not is_details_response(response):
            return None
        body = response.json()
        return body if _details_id(body) == order_id else None

    return matches


def select_rows(rows: list[OrderRow], known: set[str], first_n: int) -> tuple[list[OrderRow], bool]:
    """The rows to read, and whether more orders were new than the list's page shows."""
    if not known:
        return rows[:first_n], False
    for position, row in enumerate(rows):
        if row.id in known:
            return rows[:position], False
    return rows, len(rows) >= MAX_ORDERS  # no known order on the page: the gap may be longer


def sync_orders(
    page: Page,
    storage: "Storage",
    first_n: int,
    progress: Callable[[int, int, OrderRow], None] | None = None,
) -> SyncResult:
    """Read the orders not stored yet; `progress(i, total, row)` after each row."""
    if not 1 <= first_n <= MAX_ORDERS:
        raise ValueError(
            f"first_n must be 1..{MAX_ORDERS}: only the list's first page was observed, "
            "its next page wasn't"
        )
    with _Listener(page) as listener:
        page.goto(ORDERS_URL)
        settle(page)
        rows = rows_from_list(listener.wait_for(_list_body, "order list"))
    chosen, capped = select_rows(rows, storage.known_order_ids(), first_n)
    new = skipped = left_out = 0
    for i, row in enumerate(chosen, start=1):
        if row.status != FINISHED:
            skipped += 1
        else:
            with _Listener(page) as listener:
                page.goto(f"{ORDERS_URL}/{row.id}")
                body = listener.wait_for(_details_body_of(row.id), f"details of order {row.id}")
            order, dropped = order_and_left_out(body, row)
            storage.save_order(order)
            new += 1
            left_out += dropped
            settle(page)
        if progress:
            progress(i, len(chosen), row)
    return SyncResult(
        new=new,
        skipped=skipped,
        stored=len(storage.known_order_ids()),
        left_out_lines=left_out,
        capped=capped,
    )
