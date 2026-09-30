"""Discovery agent, session 1: learn how a store's search works (ADR-0006, ADR-0012, LLD 2.7).

A LangChain agent (LangGraph underneath) drives the discovery tools and ends with a `search`
section that the real catalog accepts for every test query. Every tool call and result is logged,
to `log` and to `data/discovery/`. When no section is accepted nothing is written to `profiles/`:
nobody writes a profile by hand to get past it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from shopping_minion.browser import BrowserProvider
from shopping_minion.catalog.profile import (
    DEFAULT_PROFILES_DIR,
    Search,
    SiteProfile,
    check_store_name,
    save_profile,
)
from shopping_minion.discovery.tools import DiscoverySession, build_tools, truncate

DEFAULT_LOG_DIR = Path("data/discovery")  # data/ is git-ignored
LOG_CHARS = 2000

SEARCH_EXAMPLE = r"""
steps:
  - open: "{base_url}busca/{query}"
results:
  wait_for: "text=/Found \\d+ items/"
  from_response:
    url_matches: "/search/results"
    items: hits
    fields:
      id: id
      name: name
      brand: brandName
      price: pricing.price
      url: "{base_url}p/{id}"
      in_stock: { path: quantity.inStock }
    unit_of_sale:
      weight_when: { path: saleUnit, equals: [KG] }
      step_g: { path: quantity.fraction, scale: 1000 }
      pack_size: { path: name, regex: "(?:c/|lv)\\s*(\\d+)" }
""".strip()

DOM_EXAMPLE = r"""
steps:
  - open: "{base_url}"
  - fill: "input[name=q]"
    value: "{query}"
  - press: Enter
    field: "input[name=q]"
results:
  wait_for: "text=/Found \\d+ items/"
  from_dom:
    item: "[data-test=product-card]"
    extract:
      id: { selector: "a.card", attr: data-id }
      name: { selector: ".name" }
      price: { selector: ".price" }
    fields: { id: id, name: name, price: price, url: "{base_url}p/{id}" }
""".strip()

_PROMPT_TEMPLATE = """\
# Your job

You are a discovery agent. Your job is to learn how a user searches on one online store and how
the search results can be read, and to submit that as a `search` section. You are not shopping:
do not look for the best product, do not compare prices, do not add anything to a cart.

# The rule

The store is used only through its site, in a browser, as a user would. Your tools open pages,
read them, click and type, and show you what the page itself received. None of them can send a
request, and you must not try to describe an API to call: no endpoint, no host, no ids, no query
parameters. The site profile describes user steps and where to read what the site shows.

How to read the results, in this order of preference:
1. `from_response`: read a response the page itself received after the search (`page_responses`
   lists them). `url_matches` is a regular expression that recognises that response by its path
   only: no host, no ids (no runs of three digits), no query values (no `=`).
2. `from_dom`: read the product cards in the page, when no response carries the products.
3. `from_script`: a JavaScript expression that only reads what the app already holds in its own
   state (a global store). Last resort. It may not send requests or contain a URL.

# The `search` section

It is YAML with two keys.
- `steps`: what a user does to search, in order. Each step has exactly one key: `open` (a page
  under `{{base_url}}`, which ends with `/`), `fill` (a selector, with `value`), `press` (a key,
  optionally with `field`), `click` (a selector or `text=...`), `wait_for` (a selector or
  `text=...`). Placeholders: `{{base_url}}` and `{{query}}`.
- `results`: `wait_for` is required. Choose something that is also present when a search finds
  nothing (for example the text that shows the number of results, "0 items"), not something that
  exists only when there are products. Then at least one of `from_response`, `from_dom`,
  `from_script`. Each has `fields` (where id, name, url, and optionally brand, size, price and
  in_stock are) and, optionally, `unit_of_sale`.

Example reading a response the page received:

```yaml
{search_example}
```

Example searching by typing in the box and reading the DOM:

```yaml
{dom_example}
```

`fields` paths are dotted paths inside one item (`pricing.price`, `images[0]`). `url` is a template
of a page of the store. In `from_dom`, `extract` names the values read inside one card and
`fields` points to those names.

# Units of sale

They matter, because the shopper's list says "2 kg of chicken" or "1 pack of toilet paper".
Look at how this site expresses them and record it in `unit_of_sale`:
- unit: one item, the default;
- pack: several units sold together. The size is often only in the product name ("c/12 rolos",
  "leve 16 pague 15"), so `pack_size` often reads the name with a regex;
- weight step: products sold by weight, where a stepper button adds a fixed amount. `weight_when`
  says which products are sold by weight, and `step_g` the grams per click (scale the number if
  the site gives kilos).
Check the products that come back (`try_search` shows unit, brand, price and stock) against what
the site shows on the page, for products of each kind you can find.

# Not allowed

Do not log in, do not touch the cart, and do not type personal data (an email, password, address,
CPF, CEP, phone). The tools refuse these; do not look for a way around them. Stay on the store's
site. If something seems to need a request to the store that a user would not make, stop and say
so in your final message instead of working around it.

# How to work

1. Explore: `open_page` the home page, `read_page`, `inspect` the search box. Search once by hand
   (`type_text`, or open the search page) and look at `page_responses` to see whether a response
   carries the products.
2. Draft a `search` section.
3. `try_search` it for each test query and read the result. Fix what is wrong: missing items,
   wrong names, prices, units of sale, a `wait_for` that times out when nothing is found.
4. `submit_search` with the section. It runs every test query and accepts only if all return
   results. If it is rejected, read why, fix, and submit again.
5. When it is accepted, reply with a short, factual report for the reviewer: which source was
   used (response, DOM or script) and why, how units of sale are read (unit, pack, weight step)
   and for which products you saw each, and anything uncertain or not checked. Do not claim
   anything you did not see. Then stop.

If you cannot produce a section that works, say why in your final message. Do not invent one.
"""

SYSTEM_PROMPT = _PROMPT_TEMPLATE.format(search_example=SEARCH_EXAMPLE, dom_example=DOM_EXAMPLE)


@dataclass(frozen=True)
class DiscoveryResult:
    accepted: Search | None
    final_message: str
    log_path: Path
    steps: int
    reason: str | None = None  # why nothing was accepted; None when a section was accepted
    profile_path: Path | None = None  # written only when a section was accepted


def query_slug(query: str) -> str:
    """`papel higiênico` -> `papel-higienico`, for fixture file names."""
    folded = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-") or "query"


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts = [
        block if isinstance(block, str) else str(block.get("text", ""))
        for block in content
        if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text")
    ]
    return "".join(parts).strip()


def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0]}" if lines else type(error).__name__


class _Recorder:
    """Sends each line to `log` and appends it to the log file."""

    def __init__(self, log: Callable[[str], Any], path: Path) -> None:
        self._log = log
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, line: str) -> None:
        self._log(line)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _write_outputs(
    session: DiscoverySession, accepted: Search, final_message: str, profiles_dir: Path
) -> Path:
    def build(notes: str | None) -> SiteProfile:
        return SiteProfile(
            store=session.store,
            version=1,
            base_url=session.base_url,
            search=accepted,
            notes=notes,
        )

    try:
        profile = build(final_message or None)
    except ValidationError:
        # The agent's report can't go in the profile (for example it quotes a header or an
        # environment variable name); the section itself was already validated. The report stays
        # in the result and in the log.
        profile = build("The agent's report was not stored here; see the discovery log.")
    path = save_profile(profile, profiles_dir)
    fixtures = path.parent / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    for query, candidates in session.candidates.items():
        data = [candidate.model_dump(mode="json") for candidate in candidates]
        (fixtures / f"candidates-{query_slug(query)}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return path


def _first_message(store: str, base_url: str, queries: list[str]) -> str:
    listed = "\n".join(f"- {query}" for query in queries)
    return (
        f"Store: {store}\nHome page: {base_url}\nTest queries:\n{listed}\n\n"
        "Learn how a user searches on this store and how the results can be read, then submit "
        "the `search` section."
    )


async def discover_search(
    store: str,
    base_url: str,
    queries: list[str],
    *,
    model: BaseChatModel,
    browser: BrowserProvider,
    log: Callable[[str], Any] = print,
    max_steps: int = 120,
    tools_factory: Callable[[DiscoverySession], list[BaseTool]] = build_tools,
    profiles_dir: Path = DEFAULT_PROFILES_DIR,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> DiscoveryResult:
    """Run the agent. A step is one model call (and the tool calls it asked for)."""
    check_store_name(store)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record = _Recorder(log, Path(log_dir) / f"{store}-{stamp}.log")
    record(f"discovery of {store} at {base_url}; queries: {queries}; max steps: {max_steps}")

    steps = 0
    final_message = ""
    reason: str | None = None
    accepted: Search | None = None
    profile_path: Path | None = None

    async with browser.session() as context:
        page = await context.new_page()
        session = DiscoverySession(context, page, store, base_url, list(queries))
        agent = create_agent(model, tools_factory(session), system_prompt=SYSTEM_PROMPT)
        inputs = {"messages": [("user", _first_message(store, base_url, queries))]}
        config = {"recursion_limit": 2 * max_steps + 10}
        try:
            async for update in agent.astream(inputs, config, stream_mode="updates"):
                for node_output in update.values():
                    for message in (node_output or {}).get("messages", []):
                        if isinstance(message, AIMessage):
                            steps += 1
                            for call in message.tool_calls:
                                args = json.dumps(call["args"], ensure_ascii=False)
                                record(f"[{steps}] call {call['name']} {truncate(args, LOG_CHARS)}")
                            text = _message_text(message)
                            if text and not message.tool_calls:
                                final_message = text
                        elif isinstance(message, ToolMessage):
                            result = truncate(_message_text(message), LOG_CHARS)
                            record(f"[{steps}] result {message.name}: {result}")
                if steps >= max_steps and session.accepted is None:
                    reason = f"step limit reached ({max_steps} steps) without an accepted section"
                    break
        except Exception as error:  # noqa: BLE001 - any model, network or graph failure ends the run and is reported
            reason = f"the run failed: {_first_line(error)}"
        accepted = session.accepted
        if accepted is not None:
            reason = None
            profile_path = _write_outputs(session, accepted, final_message, profiles_dir)
        elif reason is None:
            reason = "the agent finished without an accepted section (it gave up or stopped early)"

    record(f"steps used: {steps}")
    record(f"final message: {final_message or '(none)'}")
    if accepted is not None:
        record(f"accepted; profile written to {profile_path}")
    else:
        record(f"not accepted: {reason}; nothing was written to {profiles_dir}")
    return DiscoveryResult(accepted, final_message, record.path, steps, reason, profile_path)
