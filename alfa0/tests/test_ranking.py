from shopping_minion.catalog.ranking import rank
from shopping_minion.contracts import Candidate, UnitOfSale


def cand(id, name, in_stock=True):
    return Candidate(
        id=id, name=name, unit_of_sale=UnitOfSale(kind="unit"), in_stock=in_stock, url="https://s/x"
    )


def test_rank_prefers_in_stock_then_query_overlap():
    candidates = [
        cand("1", "Molho de tomate"),
        cand("2", "Atum ralado", in_stock=False),
        cand("3", "Atum sólido"),
    ]
    assert [c.id for c in rank("atum sólido", candidates)] == ["3", "1", "2"]


def test_rank_ignores_accents_and_case():
    assert rank("ATUM SOLIDO", [cand("1", "x"), cand("2", "Atum Sólido")])[0].id == "2"


def test_rank_caps_at_twenty():
    assert len(rank("atum", [cand(str(i), "atum") for i in range(30)])) == 20
