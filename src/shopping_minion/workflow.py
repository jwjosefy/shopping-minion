"""Shopping workflow (HLD §4.3, ADR-0009): a deterministic LangGraph graph run once per item.

    search -> decide -> execute -> done      (early exits: no candidates, not sure)

`run_list` walks the confirmed list one item at a time (the cart is shared state on a single
browser session), saves progress after every item, and stops the run when the executor says the
site changed (HLD §4.6). Nothing here talks to a store or a model directly: it uses the `Catalog`,
`DecisionBackend` and `CartExecutor` interfaces.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, TypedDict

from langgraph.graph import END, StateGraph

from shopping_minion.contracts import (
    Candidate,
    CartLine,
    ConfirmedItem,
    ConfirmedList,
    Decision,
    DecisionFlag,
    DecisionStatus,
    ReportItem,
    RunReport,
    SaleQuantity,
)
from shopping_minion.policy import DEFAULT_THRESHOLDS, Thresholds
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import DecisionBackend, resolve
from shopping_minion.runs import RunStore

log = logging.getLogger(__name__)


class SiteChangedError(RuntimeError):
    """The site no longer behaves like its profile says: stop and ask for rediscovery."""


class Catalog(Protocol):
    async def search(self, query: str) -> list[Candidate]: ...


class CartExecutor(Protocol):
    async def add_to_cart(self, candidate: Candidate, sale: SaleQuantity) -> CartLine: ...


class _State(TypedDict, total=False):
    item: ConfirmedItem
    candidates: list[Candidate]
    decision: Decision
    sale: SaleQuantity | None
    cart_line: CartLine | None


def build_item_graph(
    catalog: Catalog,
    backend: DecisionBackend,
    executor: CartExecutor,
    preferences: Preferences,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
):
    async def search(state: _State) -> _State:
        return {"candidates": await catalog.search(state["item"].name)}

    async def decide(state: _State) -> _State:
        resolution = await asyncio.to_thread(
            resolve, state["item"], state["candidates"], preferences, backend, thresholds
        )
        return {"decision": resolution.decision, "sale": resolution.sale_quantity}

    async def execute(state: _State) -> _State:
        sale = state["sale"]
        assert sale is not None
        candidate = next(c for c in state["candidates"] if c.id == sale.candidate_id)
        return {"cart_line": await executor.add_to_cart(candidate, sale)}

    def after_search(state: _State) -> str:
        return "decide" if state["candidates"] else "not_found"

    def after_decide(state: _State) -> str:
        return "execute" if state.get("sale") is not None else END

    async def not_found(state: _State) -> _State:
        item = state["item"]
        return {"decision": Decision(item=item, status=DecisionStatus.NOT_FOUND), "sale": None}

    graph = StateGraph(_State)
    graph.add_node("search", search)
    graph.add_node("decide", decide)
    graph.add_node("execute", execute)
    graph.add_node("not_found", not_found)
    graph.set_entry_point("search")
    graph.add_conditional_edges(
        "search", after_search, {"decide": "decide", "not_found": "not_found"}
    )
    graph.add_conditional_edges("decide", after_decide, {"execute": "execute", END: END})
    graph.add_edge("execute", END)
    graph.add_edge("not_found", END)
    return graph.compile()


Progress = Callable[[int, int, ReportItem | None], Awaitable[None] | None]


async def run_list(
    confirmed: ConfirmedList,
    catalog: Catalog,
    backend: DecisionBackend,
    executor: CartExecutor,
    preferences: Preferences,
    store: RunStore,
    run_id: str,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    on_progress: Progress | None = None,
    dry_run: bool = False,
) -> RunReport:
    graph = build_item_graph(catalog, backend, executor, preferences, thresholds)
    items: list[ReportItem] = []
    total = len(confirmed.items)
    stopped = False

    for index, item in enumerate(confirmed.items):
        if stopped:
            items.append(_not_attempted(item))
            continue
        try:
            state = await graph.ainvoke({"item": item})
            report_item = _report_item(state)
        except SiteChangedError as e:
            log.warning("site changed while handling %r: %s", item.name, e)
            stopped = True
            report_item = ReportItem(
                decision=Decision(
                    item=item,
                    status=DecisionStatus.FAILED,
                    flags=[DecisionFlag.REDISCOVERY_NEEDED],
                    rationale=str(e),
                )
            )
        items.append(report_item)
        await _emit(on_progress, index + 1, total, report_item)
        store.save(run_id, "report", RunReport(run_id=run_id, items=items, dry_run=dry_run))

    report = RunReport(run_id=run_id, items=items, dry_run=dry_run)
    store.save(run_id, "report", report)
    return report


def _report_item(state: _State) -> ReportItem:
    decision = state["decision"]
    by_id = {c.id: c for c in state.get("candidates", [])}
    return ReportItem(
        decision=decision,
        candidate=by_id.get(decision.candidate_id) if decision.candidate_id else None,
        alternative_candidates=[
            by_id[a.candidate_id] for a in decision.alternatives if a.candidate_id in by_id
        ],
        sale_quantity=state.get("sale"),
        cart_line=state.get("cart_line"),
    )


def _not_attempted(item: ConfirmedItem) -> ReportItem:
    return ReportItem(
        decision=Decision(
            item=item,
            status=DecisionStatus.FAILED,
            rationale="not attempted: the run stopped after the site changed",
        )
    )


async def _emit(on_progress: Progress | None, done: int, total: int, item: ReportItem) -> None:
    if on_progress is None:
        return
    result = on_progress(done, total, item)
    if result is not None:
        await result
