from decimal import Decimal

import pytest

from shopping_minion import resolver_julia
from shopping_minion.contracts import Candidate, ConfirmedItem, UnitOfSale
from shopping_minion.resolver_julia import NONE_KEY, Julia1Backend, build_state, describe


def cand(id, name, **kw):
    return Candidate(
        id=id, name=name, unit_of_sale=UnitOfSale(kind="unit"), url="https://s/x", **kw
    )


class FakeEngine:
    def __init__(self, probabilities):
        self.probabilities, self.calls = probabilities, []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        best = max(self.probabilities, key=self.probabilities.get)
        return {
            "answers": {
                "product": {
                    "type": "choice",
                    "probabilities": self.probabilities,
                    "choice": best,
                    "max_probability": self.probabilities[best],
                }
            }
        }


@pytest.fixture
def engine(monkeypatch):
    def install(probabilities):
        fake = FakeEngine(probabilities)
        monkeypatch.setattr(resolver_julia, "_engine", lambda path, device: fake)
        return fake

    return install


CANDS = [cand("100", "Atum Sólido", price=Decimal("13.98")), cand("200", "Sardinha")]


def test_picks_the_most_probable_candidate_and_lists_alternatives(engine):
    fake = engine({"c0": 0.7, "c1": 0.25, NONE_KEY: 0.05})
    choice = Julia1Backend("w").choose(ConfirmedItem(name="atum"), CANDS, None)
    assert choice.candidate_id == "100" and choice.p_product == 0.7
    assert [(a.candidate_id, a.p) for a in choice.alternatives] == [("200", 0.25)]
    assert choice.rationale is None
    options = fake.calls[0][1]["product"]["criteria"]
    assert set(options) == {"c0", "c1", NONE_KEY} and "R$ 13.98" in options["c0"]


def test_none_option_means_no_candidate(engine):
    engine({"c0": 0.1, "c1": 0.1, NONE_KEY: 0.8})
    choice = Julia1Backend("w").choose(ConfirmedItem(name="atum"), CANDS, None)
    assert choice.candidate_id is None and choice.p_product == 0.8


def test_at_most_nineteen_candidates_plus_none(engine):
    many = [cand(str(i), f"p{i}") for i in range(30)]
    fake = engine({"c0": 1.0})
    Julia1Backend("w").choose(ConfirmedItem(name="x"), many, None)
    assert len(fake.calls[0][1]["product"]["criteria"]) == 20


def test_drops_the_least_relevant_candidates_until_options_fit_the_head(monkeypatch):
    seen = []

    class TightEngine:
        def predict(self, state, questions):
            options = questions["product"]["criteria"]
            seen.append(len(options))
            if len(options) > 8:
                raise ValueError("Question/options exceed lossless head budget")
            return {"answers": {"product": {"probabilities": {"c0": 0.9, NONE_KEY: 0.1}}}}

    monkeypatch.setattr(resolver_julia, "_engine", lambda path, device: TightEngine())
    many = [cand(str(i), f"p{i}") for i in range(30)]
    choice = Julia1Backend("w").choose(ConfirmedItem(name="x"), many, None)
    assert choice.candidate_id == "0" and seen[0] == 20 and seen[-1] == 8


def test_state_carries_constraints_written_line_and_preferences():
    item = ConfirmedItem(
        name="feijão", constraints=["normal", "não preto"], source_line="Feijão normal / preto não"
    )
    state = build_state(item, {"variant": "carioca"})
    assert "não preto" in state and "Feijão normal / preto não" in state and "carioca" in state


def test_out_of_stock_is_visible_to_the_model():
    assert "OUT OF STOCK" in describe(cand("1", "x", in_stock=False))
