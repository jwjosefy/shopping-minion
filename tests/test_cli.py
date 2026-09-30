from decimal import Decimal

from shopping_minion.cli import build_parser, format_candidate
from shopping_minion.contracts import Candidate, UnitOfSale


def test_serve_defaults_and_options():
    args = build_parser().parse_args(["serve"])
    assert (args.store, args.dry_run) == ("andorinha", False)
    args = build_parser().parse_args(["serve", "--store", "x", "--dry-run"])
    assert (args.store, args.dry_run) == ("x", True)


def test_search_takes_store_and_query():
    args = build_parser().parse_args(["search", "x", "atum ralado"])
    assert (args.command, args.store, args.query) == ("search", "x", "atum ralado")


def _cand(unit, **kw):
    return Candidate(id="1", name="Atum", unit_of_sale=unit, url="https://s/1", **kw)


def test_candidate_line_has_price_unit_name_brand_and_stock():
    line = format_candidate(
        _cand(
            UnitOfSale(kind="pack", pack_size=3),
            price=Decimal("9.5"),
            brand="Gomes",
            in_stock=False,
        )
    )
    assert line == "R$ 9.50 | pack of 3 | Atum | Gomes | OUT OF STOCK"
    weight = format_candidate(_cand(UnitOfSale(kind="weight_step", step_size_g=100)))
    assert weight == "no price | weight step 100 g | Atum | -"


def test_discover_command_is_registered():
    from shopping_minion.cli import build_parser

    args = build_parser().parse_args(
        ["discover", "andorinha", "--url", "https://andorinhaonline.com.br/"]
    )
    assert args.command == "discover" and args.store == "andorinha"
