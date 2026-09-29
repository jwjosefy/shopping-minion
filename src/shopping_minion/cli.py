"""Command-line entry point: `uv run shopping-minion <command>`."""

from __future__ import annotations

import argparse
import asyncio

from shopping_minion.browser import browser_provider
from shopping_minion.catalog.adapter import fetch, parse_results, rank
from shopping_minion.catalog.profile import load_profile

ANDORINHA_URL = "https://andorinhaonline.com.br/"
V0_ITEMS = ["atum", "papel higiênico", "filé de peito de frango"]  # HLD §1


async def _browser_check(url: str, headless: bool) -> int:
    async with browser_provider(headless=headless).session() as context:
        page = await context.new_page()
        response = await page.goto(url, wait_until="domcontentloaded")
        status = response.status if response else None
        print(f"status:    {status}")
        print(f"final url: {page.url}")
        print(f"title:     {await page.title()}")
        return 0 if status is not None and status < 400 else 1


async def _search(store: str, query: str) -> int:
    profile = load_profile(store)
    if profile.search is None:
        print(f"profile {store!r} has no search section; run discover first")
        return 1
    async with browser_provider().session() as context:
        payload = await fetch(context, profile.search, query)
    candidates = rank(query, parse_results(profile.search, payload))
    for c in candidates:
        unit = c.unit_of_sale
        detail = unit.kind
        if unit.step_size_g:
            detail += f" {unit.step_size_g:g} g/step"
        if unit.pack_size:
            detail += f" x{unit.pack_size}"
        stock = "" if c.in_stock else "  [out of stock]"
        print(f"{c.price or '-':>8}  {detail:<18} {c.name} ({c.brand or '-'}){stock}")
    print(f"{len(candidates)} candidates")
    return 0 if candidates else 1


async def _discover(store: str, url: str, queries: list[str], headed: bool) -> int:
    from shopping_minion.config import load_models_config
    from shopping_minion.discovery.agent import discover_search

    role = load_models_config().discovery
    print(f"discovery with {role.provider}:{role.model} on {url}")
    result = await discover_search(store, url, queries, role, browser_provider(headless=not headed))
    print("\n" + result.report)
    if result.profile_path is None:
        return 1
    print(f"\nprofile: {result.profile_path}")
    for fixture in result.fixtures:
        print(f"fixture: {fixture}")
    print("Review the profile before using it (ADR-0006).")
    return 0


def main(argv: list[str] | None = None) -> int:
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

    discover = commands.add_parser("discover", help="learn a store's search (discovery part 1)")
    discover.add_argument("store")
    discover.add_argument("--url", default=ANDORINHA_URL)
    discover.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="test query (repeatable); defaults to the v0 items",
    )
    discover.add_argument("--headed", action="store_true")

    search = commands.add_parser("search", help="search a store using its site profile")
    search.add_argument("store")
    search.add_argument("query")

    args = parser.parse_args(argv)
    if args.command == "browser-check":
        return asyncio.run(_browser_check(args.url, headless=not args.headed))
    if args.command == "discover":
        queries = args.queries or V0_ITEMS
        return asyncio.run(_discover(args.store, args.url, queries, args.headed))
    if args.command == "search":
        return asyncio.run(_search(args.store, args.query))
    if args.command == "serve":
        import uvicorn

        from shopping_minion.web.app import create_app

        host = "0.0.0.0" if args.lan else "127.0.0.1"
        uvicorn.run(create_app(), host=host, port=args.port)
        return 0
    return 2
