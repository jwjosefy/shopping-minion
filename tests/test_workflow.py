import asyncio
from decimal import Decimal

from shopping_minion.contracts import (
    Candidate,
    CartLine,
    ConfirmedItem,
    ConfirmedList,
    DecisionFlag,
    DecisionStatus,
    ReportItem,
    RunReport,
    UnitOfSale,
)
from shopping_minion.preferences import Preferences
from shopping_minion.resolver import ProductChoice
from shopping_minion.runs import RunStore
from shopping_minion.workflow import SiteChangedError, run_list


def cand(id, name, **unit):
    return Candidate(
        id=id,
        name=name,
        unit_of_sale=UnitOfSale(**(unit or {"kind": "unit"})),
        price=Decimal(5),
        url=f"https://s/{id}",
    )


class FakeCatalog:
    def __init__(self, results):
        self.results, self.queries = results, []

    async def search(self, query):
        self.queries.append(query)
        return self.results.get(query, [])


class FakeBackend:
    name = "fake"

    def __init__(self, p):
        self.p = p

    def choose(self, item, candidates, preference):
        return ProductChoice(candidates[0].id, self.p.get(item.name, 0.9))


class FakeExecutor:
    def __init__(self, fail_on=None):
        self.added, self.fail_on = [], fail_on

    async def add_to_cart(self, candidate, sale):
        if candidate.id == self.fail_on:
            raise SiteChangedError("selector .add-to-cart not found")
        self.added.append((candidate.id, sale.steps_or_units))
        return CartLine(product_id=candidate.id, quantity=sale.steps_or_units, verified=True)


def run(items, catalog, executor, p=None, tmp_path=None):
    store = RunStore(tmp_path)
    run_id = store.create()
    progress = []

    async def on_progress(done, total, item):
        progress.append((done, total, item.decision.status))

    report = asyncio.run(
        run_list(
            ConfirmedList(items=items),
            catalog,
            FakeBackend(p or {}),
            executor,
            Preferences({}),
            store,
            run_id,
            on_progress=on_progress,
        )
    )
    return report, store, run_id, progress


def statuses(report):
    return [i.decision.status for i in report.items]


def test_every_status_path(tmp_path):
    catalog = FakeCatalog(
        {
            "atum": [cand("1", "Atum")],
            "papel": [cand("2", "Papel", kind="pack", pack_size=12)],
            "misterio": [cand("3", "Coisa")],
        }
    )
    executor = FakeExecutor()
    items = [
        ConfirmedItem(name="atum"),
        ConfirmedItem(name="papel"),
        ConfirmedItem(name="misterio"),
        ConfirmedItem(name="inexistente"),
        ConfirmedItem(name="lanches", needs_clarification=True),
    ]
    report, _, _, progress = run(
        items, catalog, executor, {"papel": 0.6, "misterio": 0.2}, tmp_path
    )

    assert statuses(report) == [
        DecisionStatus.ADDED,
        DecisionStatus.ADDED_LOW_CONFIDENCE,
        DecisionStatus.NOT_SURE,
        DecisionStatus.NOT_FOUND,
        DecisionStatus.NOT_FOUND,  # "lanches" finds nothing in the fake catalog first
    ]
    assert executor.added == [("1", 1), ("2", 1)]  # low confidence added, not-sure skipped
    assert [p[0] for p in progress] == [1, 2, 3, 4, 5] and progress[0][1] == 5


def test_clarification_item_with_candidates_is_not_sure_and_not_added(tmp_path):
    catalog = FakeCatalog({"lanches": [cand("9", "Biscoito")]})
    executor = FakeExecutor()
    report, *_ = run(
        [ConfirmedItem(name="lanches", needs_clarification=True)],
        catalog,
        executor,
        tmp_path=tmp_path,
    )
    assert statuses(report) == [DecisionStatus.NOT_SURE] and executor.added == []


def test_site_change_stops_the_run_and_flags_rediscovery(tmp_path):
    catalog = FakeCatalog({n: [cand(n, n)] for n in ("a", "b", "c")})
    executor = FakeExecutor(fail_on="b")
    items = [ConfirmedItem(name=n) for n in ("a", "b", "c")]
    report, *_ = run(items, catalog, executor, tmp_path=tmp_path)

    assert statuses(report) == [DecisionStatus.ADDED, DecisionStatus.FAILED, DecisionStatus.FAILED]
    assert DecisionFlag.REDISCOVERY_NEEDED in report.items[1].decision.flags
    assert "not attempted" in report.items[2].decision.rationale
    assert catalog.queries == ["a", "b"]  # "c" was never searched
    assert report.counts[DecisionStatus.FAILED] == 2


def test_report_is_saved_to_the_run_folder(tmp_path):
    catalog = FakeCatalog({"atum": [cand("1", "Atum")]})
    _, store, run_id, _ = run(
        [ConfirmedItem(name="atum")], catalog, FakeExecutor(), tmp_path=tmp_path
    )
    saved = store.load(run_id, "report", RunReport)
    assert saved is not None and isinstance(saved.items[0], ReportItem)
    assert saved.items[0].cart_line.verified


def test_report_carries_candidate_details_and_total(tmp_path):
    catalog = FakeCatalog({"atum": [cand("1", "Atum Sólido")], "papel": [cand("2", "Papel")]})
    report, *_ = run(
        [ConfirmedItem(name="atum"), ConfirmedItem(name="papel", quantity=2, unit="un")],
        catalog,
        FakeExecutor(),
        tmp_path=tmp_path,
    )
    assert report.items[0].candidate.name == "Atum Sólido"
    assert report.cart_total == Decimal(15)  # 5 x 1 + 5 x 2
