"""The whole `run` flow offline: fakes for the browser, search, cart and Jev's client."""

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
import yaml

from shopping_minion import run as run_module
from shopping_minion import workflow as workflow_module
from shopping_minion.browser import NotLoggedInError
from shopping_minion.cli import build_parser
from shopping_minion.items import Candidate, CartResult, Decision, Item
from shopping_minion.storage import Storage


def cand(pid, name, unit="un", step_kg=None, price="9.50", available=True):
    return Candidate(
        product_id=pid,
        slug=f"slug-{pid}",
        name=name,
        brand=None,
        price=Decimal(price),
        list_price=None,
        unit_of_sale=unit,
        step_kg=step_kg,
        available=available,
    )


ATUM = [cand("1", "Atum Gomes 170g"), cand("2", "Atum Coqueiro 170g", price="8.90")]
PAPEL = [
    cand("10", "Papel Neve 12un", price="20.00"),
    cand("11", "Papel Personal 8un", price="15.00"),
    cand("12", "Papel Sem Estoque", available=False),
]
FRANGO = [cand("20", "Filé de Peito de Frango kg", unit="kg", step_kg=0.1, price="21.90")]
CANDIDATES = {"atum": ATUM, "papel higienico": PAPEL, "file de peito de frango": FRANGO}

LIST = {
    "items": [
        {"source_line": "atum", "name": "atum", "search_term": "atum"},
        {
            "source_line": "papel higiênico",
            "name": "papel higiênico",
            "search_term": "papel higienico",
        },
        {
            "source_line": "filé de peito de frango 1 kg",
            "name": "filé de peito de frango",
            "search_term": "file de peito de frango",
            "quantity": {"value": 1, "unit": "kg"},
        },
    ]
}


class FakeClient:
    """Jev stand-in: answers each question from `answers[item name] = (label, confidence)`."""

    def __init__(self, answers):
        self.answers = answers

    def system_one(self, state, questions, model=None):
        choices = {}
        for key, question in questions.items():
            label, confidence = self.answers[question.instructions["item"]["name"]]
            choices[key] = SimpleNamespace(
                choice=label, confidence=confidence, probabilities={label: confidence}
            )
        return SimpleNamespace(choices=choices, model="jev-1.13.0")


class Script:
    """input_fn that replays answers and remembers the prompts; print_fn that keeps lines."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
        self.lines = []

    def input(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)

    def print(self, *args, **_kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    @property
    def text(self):
        return "\n".join(self.lines)


class World:
    """Everything the run touches, faked, with a record of what was called."""

    def __init__(self, tmp_path, monkeypatch, *, logged_in=True, answers=None, candidates=None):
        self.tmp_path = tmp_path
        self.db = tmp_path / "run.sqlite"
        self.prefs = tmp_path / "prefs.yaml"
        self.prefs.write_text("{}", encoding="utf-8")
        self.config = tmp_path / "decide.yaml"
        self.config.write_text(
            "model: jev-latest\nbatch_size: 5\naccept_at: 0.8\nask_below: 0.5\n", encoding="utf-8"
        )
        self.list_path = tmp_path / "lista.yaml"
        self.list_path.write_text(yaml.safe_dump(LIST, allow_unicode=True), encoding="utf-8")
        self.answers = answers or {
            "atum": ("p1", 0.9),  # accepted
            "papel higiênico": ("p10", 0.6),  # ask
            "filé de peito de frango": ("p20", 0.95),  # accepted
        }
        self.candidates = candidates or CANDIDATES
        self.cart_calls = []
        self.drawer_opened = False
        self.browser_closed = False
        self.searched = False

        @contextmanager
        def open_browser_fn():
            try:
                yield None, SimpleNamespace(new_page=lambda: "page")
            finally:
                self.browser_closed = True

        self.open_browser_fn = open_browser_fn

        def ensure_logged_in(page):
            if not logged_in:
                raise NotLoggedInError("Not logged in: run `shopping-minion login`.")

        def search_all(page, items, progress=None):
            self.searched = True
            results = []
            for i, item in enumerate(items, start=1):
                found = self.candidates[item.search_term]
                results.append(found)
                progress(i, len(items), item, found)
            return results

        def add_all(page, targets, progress=None, should_stop=None):
            self.cart_calls.append(targets)
            results = []
            for i, (candidate, target) in enumerate(targets, start=1):
                result = CartResult(
                    product_id=candidate.product_id,
                    status="added",
                    quantity_shown=str(target.clicks),
                    message=None,
                )
                results.append(result)
                progress(i, len(targets), candidate, result)
            return results

        # first read: before the cart pass; second: at the end
        self.drawer_reads = [[], [("Atum Gomes 170g", "1"), ("Filé de Peito de Frango kg", "1kg")]]

        def read_cart_drawer(page):
            self.drawer_opened = True
            return self.drawer_reads.pop(0)

        monkeypatch.setattr(run_module, "ensure_logged_in", ensure_logged_in)
        monkeypatch.setattr(workflow_module, "search_all", search_all)
        monkeypatch.setattr(workflow_module, "add_all", add_all)
        monkeypatch.setattr(workflow_module, "read_cart_drawer", read_cart_drawer)

    def run(self, script, **kwargs):
        return run_module.run(
            self.list_path,
            prefs_path=self.prefs,
            db_path=self.db,
            input_fn=script.input,
            print_fn=script.print,
            open_browser_fn=self.open_browser_fn,
            client_factory=lambda: FakeClient(self.answers),
            config_path=self.config,
            **kwargs,
        )

    def rows(self, table, column):
        conn = sqlite3.connect(self.db)
        try:
            return [
                json.loads(r[0])
                for r in conn.execute(f"SELECT {column} FROM {table} ORDER BY idx")  # noqa: S608
            ]
        finally:
            conn.close()

    def status(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT status, photo FROM runs").fetchall()
        finally:
            conn.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


# --- the happy paths --------------------------------------------------------------------------


def test_ordered_candidates_pick_then_offers_then_rest_by_probability():
    def c(pid, list_price=None):
        return cand(pid, f"P{pid}").model_copy(
            update={"list_price": Decimal(list_price) if list_price else None}
        )

    decision = Decision(
        item=Item(source_line="x", name="x", search_term="x"),
        candidates=[
            c("1"),
            c("2", "12"),
            c("3"),
            c("4", "12"),
            c("5", "9.50"),
            c("6"),
            c("7", "12"),
        ],
        choice="3",
        confidence=0.6,
        probabilities={"1": 0.1, "2": 0.1, "3": 0.5, "4": 0.2, "5": 0.1, "6": 0.1},
        status="ask",
    )
    # 3 is the pick; offers 4 (0.2), 2 (0.1), 7 (missing = 0); then 1, 5 (list_price == price, so
    # no offer), 6 at 0.1 in search order
    assert [x.product_id for x in run_module._ordered_candidates(decision)] == [
        "3",
        "4",
        "2",
        "7",
        "1",
        "5",
        "6",
    ]
    no_pick = decision.model_copy(update={"choice": None, "probabilities": {}})
    assert [x.product_id for x in run_module._ordered_candidates(no_pick)] == [
        "2",
        "4",
        "7",
        "1",
        "3",
        "5",
        "6",
    ]


def test_ask_then_user_picks_then_confirms_and_cart_gets_the_right_clicks(world):
    # papel is `ask` (0.6); the user types junk, then 2 (Jev's pick is listed first, so 2 is
    # the first of the others); then "s" at the confirm prompt; Enter closes the window.
    script = Script("abc", "2", "s", "")
    assert world.run(script) == 0

    (targets,) = world.cart_calls
    by_id = {c.product_id: t for c, t in targets}
    assert set(by_id) == {"1", "11", "20"}  # atum accepted, papel picked, frango accepted
    assert by_id["20"].clicks == 10  # 1 kg at 0.1 kg per click
    assert by_id["20"].flags == []
    assert by_id["1"].clicks == 1 and by_id["1"].flags == ["QUANTITY_ASSUMED"]

    assert script.prompts == [
        "    escolha [0-3]: ",
        "    escolha [0-3]: ",
        "adicionar ao carrinho? [s/N] ",
        "",
    ]
    assert world.drawer_opened and world.browser_closed
    assert run_module.OPEN_MESSAGE in script.lines


def test_transcript(world):
    script = Script("2", "s", "")
    world.run(script)
    print("\n" + script.text)  # shown with `pytest -s`; the lines below pin the shape
    lines = script.lines
    assert "[1/3] atum: 2 resultados" in lines
    assert "[3/3] file de peito de frango: 1 resultados" in lines
    assert "[1/1] papel higiênico  (lista: 'papel higiênico')" in lines
    assert "    1. Papel Neve 12un, R$ 20,00, vendido por unidade  <- escolha do Jev" in lines
    assert "    3. Papel Sem Estoque, R$ 9,50, vendido por unidade, indisponível" in lines
    assert "    0. pular este item" in lines
    assert any(
        line.startswith("  filé de peito de frango | Filé de Peito de Frango kg | R$ 21,90/kg")
        and line.endswith("1 kg -> 10 cliques")
        for line in lines
    )
    assert any("atum | Atum Gomes 170g" in line and "[QUANTITY_ASSUMED]" in line for line in lines)
    assert "[3/3] Filé de Peito de Frango kg: added" in lines
    assert "Conferência do carrinho (lido do site depois de recarregar):" in lines
    assert "  !! papel higiênico | Papel Personal 8un | esperado 1: FALTANDO no carrinho" in lines
    assert "  2 de 3 itens da lista conferem." in lines
    assert not any("NÃO são desta lista" in line for line in lines)


def test_yes_skips_the_confirm_prompt(world):
    script = Script("1", "")  # only the ask resolution and the final Enter
    assert world.run(script, yes=True) == 0
    assert "adicionar ao carrinho? [s/N] " not in script.prompts
    assert len(world.cart_calls) == 1


def test_everything_accepted_needs_no_resolution(tmp_path, monkeypatch):
    world = World(
        tmp_path,
        monkeypatch,
        answers={
            "atum": ("p2", 0.85),
            "papel higiênico": ("p11", 0.9),
            "filé de peito de frango": ("p20", 0.99),
        },
    )
    script = Script("sim", "")
    assert world.run(script) == 0
    assert script.prompts == ["adicionar ao carrinho? [s/N] ", ""]


# --- resolution -------------------------------------------------------------------------------


def test_user_choice_is_saved_as_user_chosen_without_confidence(world):
    world.run(Script("2", "s", ""))
    decisions = world.rows("decisions", "decision_json")
    papel = decisions[1]
    assert papel["status"] == "user_chosen"
    assert papel["choice"] == "11"
    assert papel["confidence"] is None
    assert [d["status"] for d in decisions] == ["accepted", "user_chosen", "accepted"]
    assert decisions[0]["model"] == "jev-1.13.0"


def test_no_match_then_skip(tmp_path, monkeypatch):
    world = World(
        tmp_path,
        monkeypatch,
        answers={
            "atum": ("p1", 0.9),
            "papel higiênico": ("nenhum", 0.9),  # no_match
            "filé de peito de frango": ("p20", 0.95),
        },
    )
    script = Script("0", "s", "")
    assert world.run(script) == 0
    assert "    Jev: nenhum destes parece servir." in script.lines
    (targets,) = world.cart_calls
    assert {c.product_id for c, _ in targets} == {"1", "20"}
    assert [d["status"] for d in world.rows("decisions", "decision_json")] == [
        "accepted",
        "skipped",
        "accepted",
    ]
    assert "  papel higiênico | (pulado)" in script.lines


def test_nothing_fit_flag_is_shown(tmp_path, monkeypatch):
    world = World(
        tmp_path,
        monkeypatch,
        answers={
            "atum": ("p1", 0.9),
            "papel higiênico": ("p10", 0.3),  # below ask_below
            "filé de peito de frango": ("p20", 0.95),
        },
    )
    script = Script("0", "n")
    world.run(script)
    assert "    Jev: nada parece servir (confiança baixa)." in script.lines


def test_item_without_results_is_skipped_without_a_prompt(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, candidates={**CANDIDATES, "papel higienico": []})
    script = Script("s", "")
    assert world.run(script) == 0
    assert "    sem resultados na loja; item pulado." in script.lines
    assert script.prompts == ["adicionar ao carrinho? [s/N] ", ""]


def test_everything_skipped_adds_nothing(world):
    world.answers["atum"] = ("nenhum", 0.9)
    world.answers["filé de peito de frango"] = ("nenhum", 0.9)
    # three items to resolve, all skipped
    script = Script("0", "0", "0")
    assert world.run(script) == 0
    assert world.cart_calls == []
    assert world.status()[0][0] == "nothing_to_add"
    assert "nada a adicionar ao carrinho." in script.lines


# --- declining, login, input errors -----------------------------------------------------------


def test_declining_at_the_confirm_prompt_adds_nothing(world):
    script = Script("1", "")  # resolve papel, then Enter (= no)
    assert world.run(script) == 0
    assert world.cart_calls == []
    assert not world.drawer_opened
    assert world.status() == [("declined", str(world.list_path))]
    assert world.rows("cart", "result_json") == []
    assert len(world.rows("decisions", "decision_json")) == 3  # the decisions are kept
    assert world.browser_closed


def test_not_logged_in_exits_1_without_searching(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, logged_in=False)
    script = Script()  # no prompt may happen
    assert world.run(script) == 1
    assert "Not logged in: run `shopping-minion login`." in script.lines
    assert not world.searched
    assert world.status() == [("not_logged_in", str(world.list_path))]


def test_invalid_list_exits_1_before_opening_anything(world):
    world.list_path.write_text("items: [{name: atum}]", encoding="utf-8")
    script = Script()
    assert world.run(script) == 1
    assert script.lines[0].startswith("lista inválida:")
    assert not world.db.exists()


def test_a_list_that_is_not_the_ocr_format_is_rejected(world):
    world.list_path.write_text("- atum\n", encoding="utf-8")
    script = Script()
    assert world.run(script) == 1
    assert "items" in script.text


def test_eof_at_a_prompt_stops_the_run_without_adding(world):
    def eof(_prompt=""):
        raise EOFError

    code = run_module.run(
        world.list_path,
        prefs_path=world.prefs,
        db_path=world.db,
        input_fn=eof,
        print_fn=lambda *a, **k: None,
        open_browser_fn=world.open_browser_fn,
        client_factory=lambda: FakeClient(world.answers),
        config_path=world.config,
    )
    assert code == 130
    assert world.cart_calls == []
    assert world.status()[0][0] == "interrupted"


# --- storage ----------------------------------------------------------------------------------


def test_storage_rows_are_written_for_every_stage(world):
    assert world.run(Script("1", "s", "")) == 0
    assert world.status() == [("done", str(world.list_path))]

    items = world.rows("items", "ocr_json")
    assert [i["name"] for i in items] == ["atum", "papel higiênico", "filé de peito de frango"]
    assert world.rows("items", "confirmed_json") == items

    decisions = world.rows("decisions", "decision_json")
    assert [d["status"] for d in decisions] == ["accepted", "user_chosen", "accepted"]
    assert len(decisions[1]["candidates"]) == 3

    cart = world.rows("cart", "result_json")
    assert [(r["product_id"], r["status"]) for r in cart] == [
        ("1", "added"),
        ("10", "added"),
        ("20", "added"),
    ]

    storage = Storage(world.db)
    log = [r["data"] for r in storage.read_log(1)]
    storage.close()
    states = [r["state"] for r in log if "state" in r]
    assert states == ["searching", "deciding", "picking", "reviewing_cart", "filling_cart", "done"]
    (pick,) = [r for r in log if "jev_choice" in r]
    assert pick["item"] == "papel higiênico" and pick["chosen"] == "10"


# --- quantity ---------------------------------------------------------------------------------


def test_quantity_comes_from_preferences_when_the_list_has_none(world):
    world.prefs.write_text(
        yaml.safe_dump({"atum": {"quantidade": {"valor": 3, "unidade": "un"}}}), encoding="utf-8"
    )
    world.run(Script("1", "s", ""))
    (targets,) = world.cart_calls
    atum = next(t for c, t in targets if c.product_id == "1")
    assert atum.clicks == 3 and atum.flags == []


def test_inexact_kg_conversion_is_flagged(world):
    world.list_path.write_text(
        yaml.safe_dump(
            {
                "items": [
                    {
                        "source_line": "frango 250 g",
                        "name": "filé de peito de frango",
                        "search_term": "file de peito de frango",
                        "quantity": {"value": 250, "unit": "g"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    world.run(Script("s", ""))
    ((_, target),) = world.cart_calls[0]
    assert target.clicks == 3  # ceil(0.25 / 0.1)
    assert target.flags == ["QUANTITY_INEXACT"]


# --- duplicates -------------------------------------------------------------------------------


def test_duplicate_lines_are_merged_in_the_table_the_cart_and_the_check(world):
    requeijao = {"source_line": "requeijão", "name": "requeijão", "search_term": "requeijao"}
    world.list_path.write_text(
        yaml.safe_dump({"items": [requeijao, requeijao]}, allow_unicode=True), encoding="utf-8"
    )
    world.candidates = {"requeijao": [cand("30", "Requeijão Catupiry 200g")]}
    world.answers = {"requeijão": ("p30", 0.95)}
    # before: empty; after: the cart has 2 (the old bug left 1 and said ok for both lines)
    world.drawer_reads = [[], [("Requeijão Catupiry 200g", "2")]]

    script = Script("s", "")
    assert world.run(script) == 0

    (targets,) = world.cart_calls
    ((candidate, target),) = targets  # one product, one add
    assert candidate.product_id == "30" and target.clicks == 2
    assert target.flags == ["QUANTITY_ASSUMED"]
    assert any(
        line.startswith("  requeijão ×2 (2 linhas da lista) | Requeijão Catupiry 200g")
        and line.endswith("2 un -> 2 cliques  [QUANTITY_ASSUMED]")
        for line in script.lines
    )
    assert "  ok requeijão ×2 | Requeijão Catupiry 200g | esperado 2: ok" in script.lines
    assert "  1 de 1 itens da lista conferem." in script.lines
    # both decisions are stored as made; the merge is only in the cart
    assert len(world.rows("decisions", "decision_json")) == 2
    assert len(world.rows("cart", "result_json")) == 1


# --- cli --------------------------------------------------------------------------------------


def test_cli_wires_the_run_command():
    args = build_parser().parse_args(["run", "lista.yaml", "--yes", "--db", "x.sqlite"])
    assert args.func.__name__ == "_cmd_run"
    assert args.yes is True
    assert str(args.db) == "x.sqlite"
    assert str(args.preferences) == "data/preferencias.yaml"
