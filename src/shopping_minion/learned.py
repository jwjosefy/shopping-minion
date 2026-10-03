"""What Johann's own picks say about an item (LLD-M5 section 4.1).

Pure reading of the stored runs, no model call: the final product of each item in the runs
from `learn_from_run` on, counted by product under the item's normalized name.
"""

from datetime import date, datetime

from shopping_minion.history import norm
from shopping_minion.items import Contract
from shopping_minion.storage import Storage


class LearnedPreference(Contract):
    product_id: str
    times: int  # runs in which this product was the final one for the item
    last_at: date  # the newest of them, local date


def _local_date(created_at: str) -> date:
    """`runs.created_at` is UTC text; the date shown is this machine's local one."""
    return datetime.fromisoformat(created_at).astimezone().date()


def learned_preferences(
    storage: Storage, learn_from_run: int, exclude_run: int | None = None
) -> dict[str, dict[str, LearnedPreference]]:
    """`norm(item.name)` -> product id -> preference. A product counts once per item per run.

    The final product is the `choice` of an `accepted` or `user_chosen` decision, unless a
    `cart_edit` row of that run removed the line (its `line_id` is the product id). Skipped
    and unresolved items count for nothing."""
    found: dict[str, dict[str, LearnedPreference]] = {}
    for run in storage.list_runs():
        run_id = run["id"]
        if run_id < learn_from_run or run_id == exclude_run:
            continue
        removed = {
            row["data"]["line_id"]
            for row in storage.read_log(run_id)
            if row["kind"] == "cart_edit" and row["data"].get("remove")
        }
        day = _local_date(run["created_at"])
        counted: set[tuple[str, str]] = set()
        for decision in storage.read_decisions(run_id):
            product_id = decision.choice
            if (
                decision.status not in ("accepted", "user_chosen")
                or product_id is None
                or product_id in removed
            ):
                continue
            key = norm(decision.item.name)
            if (key, product_id) in counted:
                continue
            counted.add((key, product_id))
            products = found.setdefault(key, {})
            previous = products.get(product_id)
            products[product_id] = LearnedPreference(
                product_id=product_id,
                times=1 if previous is None else previous.times + 1,
                last_at=day if previous is None else max(previous.last_at, day),
            )
    return found
