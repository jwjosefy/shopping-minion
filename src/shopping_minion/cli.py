"""Command-line entry point: `uv run shopping-minion <command>`."""

from __future__ import annotations

import argparse
import asyncio

from shopping_minion.browser import browser_provider
from shopping_minion.catalog.browser_catalog import BrowserCatalog
from shopping_minion.catalog.profile import load_profile
from shopping_minion.contracts import Candidate

ANDORINHA_URL = "https://andorinhaonline.com.br/"


async def _browser_check(url: str, headless: bool) -> int:
    async with browser_provider(headless=headless).session() as context:
        page = await context.new_page()
        response = await page.goto(url, wait_until="domcontentloaded")
        status = response.status if response else None
        print(f"status:    {status}")
        print(f"final url: {page.url}")
        print(f"title:     {await page.title()}")
        return 0 if status is not None and status < 400 else 1


def describe_unit(candidate: Candidate) -> str:
    unit = candidate.unit_of_sale
    if unit.kind == "pack":
        return f"pack of {unit.pack_size}"
    if unit.kind == "weight_step":
        return f"weight step {unit.step_size_g:g} g"
    return "unit"


def format_candidate(candidate: Candidate) -> str:
    price = f"R$ {candidate.price:.2f}" if candidate.price is not None else "no price"
    parts = [price, describe_unit(candidate), candidate.name, candidate.brand or "-"]
    if not candidate.in_stock:
        parts.append("OUT OF STOCK")
    return " | ".join(parts)


async def _search(store: str, query: str) -> int:
    try:
        profile = load_profile(store)  # before the browser starts: a missing profile fails fast
    except FileNotFoundError:
        print(
            f"store {store!r} hasn't been discovered yet. "
            f"Run: shopping-minion discover {store} --url <home page>"
        )
        return 1
    async with browser_provider().session(store=store) as context:
        catalog = BrowserCatalog(profile, context)
        try:
            candidates = await catalog.search(query)
        finally:
            await catalog.close()
    for candidate in candidates:
        print(format_candidate(candidate))
    if not candidates:
        print("no results")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """One place to register commands; other modules add theirs with `add_parser(subparsers)`."""
    parser = argparse.ArgumentParser(prog="shopping-minion")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("browser-check", help="open a page with the configured browser")
    check.add_argument("url", nargs="?", default=ANDORINHA_URL)
    check.add_argument("--headed", action="store_true", help="show the browser window")

    serve = commands.add_parser("serve", help="start the web app")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--lan", action="store_true", help="listen on all interfaces so a phone can connect"
    )
    serve.add_argument("--store", default="andorinha", help="which store's profile to use")
    serve.add_argument(
        "--dry-run", action="store_true", help="decide what to add, without adding to the cart"
    )

    search = commands.add_parser("search", help="search a store and print the candidates")
    search.add_argument("store")
    search.add_argument("query")

    from shopping_minion.discovery.cli import add_parser as add_discover_parser

    add_discover_parser(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "browser-check":
        return asyncio.run(_browser_check(args.url, headless=not args.headed))
    if args.command == "search":
        return asyncio.run(_search(args.store, args.query))
    if args.command == "discover":
        from shopping_minion.discovery.cli import run as run_discover

        return asyncio.run(run_discover(args))
    if args.command == "serve":
        import uvicorn

        from shopping_minion.web.app import create_app

        host = "0.0.0.0" if args.lan else "127.0.0.1"
        uvicorn.run(
            create_app(store_name=args.store, dry_run=args.dry_run), host=host, port=args.port
        )
        return 0
    return 2
