"""Discovery agent, part 1: learn how a store's search works and write it into the site profile.

Runs once per store, supervised, before any shopping session (ADR-0006). The agent explores with
read-only browser tools and iterates on a search spec until the real adapter returns sensible
candidates for every test query. A human reviews the resulting profile before it's used.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent

from shopping_minion.browser import BrowserProvider
from shopping_minion.catalog.profile import PROFILES_DIR, HttpSearch, SiteProfile, save_profile
from shopping_minion.config import ChatRole
from shopping_minion.discovery.tools import DiscoverySession, build_tools, registrable_domain
from shopping_minion.models import chat_model

MAX_STEPS = 120  # LangGraph recursion limit: roughly 60 tool calls

SYSTEM_PROMPT = """\
You are the discovery agent of a grocery-shopping system. Your job in this session is narrow: \
find out how the store's product search works and describe it as a search spec that a \
deterministic HTTP adapter can execute. You are not shopping.

Rules (enforced by your tools, but follow them anyway):
- Stay on the store's domain. Don't log in, don't add anything to a cart, don't fill addresses, \
CEP, e-mail or personal data. If a modal asks for them, close it; if search is impossible \
without them, stop and explain why.
- Prefer a JSON/GraphQL API the site's own pages call over scraping HTML. Search from the site's \
search box, then use network_log/network_entry to find the call that returns the products.
- Never put cookies, Authorization headers or tokens in the spec. If the API only works with \
them, stop and explain.

Where the search API lives: the site's own pages may call an API on another host. Look at the network log for the call that returns the product list. If the call \
is a browser-only one (it fails outside a page, e.g. CORS or a bot-protection 403), use \
`transport: page` with `page_url` set to a store page: the request is then made with fetch() \
from inside that page. Use `transport: request` only when a plain HTTP call works.

Search spec (YAML) schema:
  kind: http
  transport: request | page    # default request
  page_url: https://store.com/  # required when transport is page
  method: GET | POST
  url: https://...            # {query} is URL-encoded in; {limit} is the page size
  headers: {}                  # only non-secret headers the API needs (e.g. content-type)
  body: <JSON>                 # for POST; any string may contain {query} and {limit}
  results_path: data.search.products     # dotted path, [n] for list indexes
  fields:                      # paths relative to one result item
    id: id
    name: name
    url: https://store.com/produto/{slug}   # template; {...} are paths in the item
    brand: brand.name          # optional
    size: packageSize          # optional, free text
    price: price.value         # optional
    in_stock: {path: available, equals: [true]}   # optional condition
  unit_of_sale:                # how the product is sold
    weight_when: {path: unit, equals: [KG]}       # condition: sold by weight
    step_g: {path: weightStep, scale: 1000}       # grams per +/- step when sold by weight
    pack_size: {path: name, regex: '(\\d+)\\s*rolos'}   # units per pack, e.g. from the name

A condition is {path, equals: [...]} or {path, matches: regex}. A value spec is \
{path, regex?, scale?, constant?}; `path: name` reads from the product name.

Units of sale matter most: an item is `unit` (one can of tuna), `pack` (toilet paper, 12 rolls) \
or `weight_step` (meat sold by weight in +/- steps of N grams). Look at how the API expresses \
this; open a product sold by weight to confirm the step size.

Work loop: explore -> draft a spec -> try_search_spec for each test query -> fix -> \
submit_search_spec. When it's accepted, reply with a short report for the human reviewer: what \
the API is, how units of sale are expressed, anything uncertain. Keep it factual.
"""


@dataclass
class DiscoveryResult:
    profile: SiteProfile | None
    report: str
    profile_path: Path | None
    fixtures: list[Path]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


async def discover_search(
    store: str,
    base_url: str,
    queries: list[str],
    role: ChatRole,
    browser: BrowserProvider,
    log: Callable[[str], None] = print,
    api_domains: list[str] | None = None,
    headed: bool = False,
) -> DiscoveryResult:
    model = chat_model(role)

    async with browser.session() as context:
        page = await context.new_page()
        session = DiscoverySession(
            context, page, registrable_domain(base_url), queries, api_domains or []
        )
        page.on("response", session.record)
        agent = create_agent(model, tools=build_tools(session), system_prompt=SYSTEM_PROMPT)

        task = (
            f"Store: {store}\nHome page: {base_url}\nTest queries: {json.dumps(queries, ensure_ascii=False)}\n"
            "Start by opening the home page."
        )
        report = ""
        async for update in agent.astream(
            {"messages": [{"role": "user", "content": task}]},
            config={"recursion_limit": MAX_STEPS},
            stream_mode="updates",
        ):
            for message in _messages(update):
                for call in getattr(message, "tool_calls", None) or []:
                    args = json.dumps(call["args"], ensure_ascii=False)
                    log(f"→ {call['name']}({args[:160]})")
                if message.type == "tool":
                    log(f"  ← {_text(message.content)[:300]!r}")
                if message.type == "ai" and not getattr(message, "tool_calls", None):
                    report = _text(message.content)

    if session.accepted is None:
        return DiscoveryResult(None, report or "no search spec was accepted", None, [])

    profile = SiteProfile(
        store=store,
        version=1,
        base_url=base_url,
        headed=headed,
        search=session.accepted,
        notes=report,
    )
    path = save_profile(profile)
    fixtures = _save_fixtures(store, session.accepted, session.payloads)
    return DiscoveryResult(profile, report, path, fixtures)


def _save_fixtures(store: str, spec: HttpSearch, payloads: dict[str, Any]) -> list[Path]:
    """Public search responses, recorded for the adapter's offline regression tests (ADR-0004)."""
    directory = PROFILES_DIR / store / "fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for query, payload in payloads.items():
        path = directory / f"search-{_slug(query)}.json"
        path.write_text(
            json.dumps({"query": query, "payload": payload}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths.append(path)
    return paths


def _messages(update: dict[str, Any]) -> list[Any]:
    messages = []
    for node_update in update.values():
        if isinstance(node_update, dict):
            messages.extend(node_update.get("messages", []))
    return messages


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)
