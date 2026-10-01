"""The `discover` command: run the discovery agent for a store's search (session 1)."""

from __future__ import annotations

import argparse
from typing import Any

import yaml

from shopping_minion.browser import browser_provider
from shopping_minion.config import load_models_config
from shopping_minion.discovery.agent import discover_search
from shopping_minion.models import chat_model

DEFAULT_QUERIES = ["atum", "papel higiênico", "filé de peito de frango"]
DEFAULT_MAX_STEPS = 120


def add_parser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "discover", help="run the discovery agent to learn a store's search (paid model calls)"
    )
    parser.add_argument("store", help="store name: lowercase letters, digits and hyphens")
    parser.add_argument("--url", required=True, help="the store's home page")
    parser.add_argument(
        "--query",
        action="append",
        default=None,
        help=f"a test query, repeatable (default: {', '.join(DEFAULT_QUERIES)})",
    )
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    return parser


async def run(args: argparse.Namespace) -> int:
    queries = args.query or DEFAULT_QUERIES
    base_url = args.url if args.url.endswith("/") else args.url + "/"
    role = load_models_config().discovery
    print(f"provider: {role.provider}")
    print(f"model:    {role.model}")
    print(f"store:    {args.store} ({base_url})")
    print(f"queries:  {queries}; max steps: {args.max_steps}")
    result = await discover_search(
        args.store,
        base_url,
        queries,
        model=chat_model(role),
        browser=browser_provider(),
        max_steps=args.max_steps,
    )
    print()
    print(f"steps used: {result.steps}")
    print(f"log:        {result.log_path}")
    print(f"final message:\n{result.final_message or '(none)'}")
    if result.accepted is None:
        print(f"\nnot accepted: {result.reason}")
        return 1
    print(f"\naccepted; profile: {result.profile_path}")
    section = result.accepted.model_dump(mode="json", exclude_none=True)
    print(yaml.safe_dump(section, sort_keys=False, allow_unicode=True, width=100))
    return 0
