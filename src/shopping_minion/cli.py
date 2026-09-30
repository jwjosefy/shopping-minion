"""Command-line entry point: `uv run shopping-minion <command>`."""

from __future__ import annotations

import argparse
import asyncio

from shopping_minion.browser import browser_provider

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

    args = parser.parse_args(argv)
    if args.command == "browser-check":
        return asyncio.run(_browser_check(args.url, headless=not args.headed))
    if args.command == "serve":
        import uvicorn

        from shopping_minion.web.app import create_app

        host = "0.0.0.0" if args.lan else "127.0.0.1"
        uvicorn.run(create_app(), host=host, port=args.port)
        return 0
    return 2
