"""Browser tools for the discovery agent (ADR-0006).

Discovery part 1 (M2) is read-only: the agent can look at pages, type into search boxes, follow
links and read network traffic, but it can't log in, touch the cart, or leave the store's domain.
The guards live here, in code, not only in the prompt.
"""

from __future__ import annotations

import functools
import inspect as pyinspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import yaml
from langchain_core.tools import BaseTool, tool
from playwright.async_api import BrowserContext, Page, Request, Response
from playwright.async_api import Error as PlaywrightError

from shopping_minion.catalog.adapter import ProfileError, fetch, parse_results, rank
from shopping_minion.catalog.profile import FORBIDDEN_HEADERS, HttpSearch

BLOCKED_ACTION = re.compile(
    r"carrinho|comprar|adicionar|finalizar|checkout|pagamento|pagar|cart|buy|add to|"
    r"entrar|login|cadastr|senha|password|sign in",
    re.IGNORECASE,
)
PAGE_WAIT_MS = 3000


@dataclass
class NetworkEntry:
    method: str
    url: str
    status: int
    request_body: str
    response_body: str
    request_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoverySession:
    context: BrowserContext
    page: Page
    allowed_domain: str
    test_queries: list[str]
    api_domains: list[str] = field(default_factory=list)  # store-owned API hosts, set by a human
    network: list[NetworkEntry] = field(default_factory=list)
    accepted: HttpSearch | None = None
    payloads: dict[str, Any] = field(default_factory=dict)

    def allowed(self, url: str) -> bool:
        """Browsing is limited to the store's domain."""
        return _on_domain(url, self.allowed_domain)

    def allowed_for_search(self, url: str) -> bool:
        """Search specs may also call API hosts a human explicitly allowed (read-only GET/POST)."""
        return any(_on_domain(url, d) for d in [self.allowed_domain, *self.api_domains])

    async def record(self, response: Response) -> None:
        request: Request = response.request
        if request.resource_type not in ("xhr", "fetch"):
            return
        if "json" not in (response.headers.get("content-type") or ""):
            return
        try:
            body = await response.text()
        except Exception:  # noqa: BLE001 - bodies of redirects/aborted requests aren't readable
            body = ""
        self.network.append(
            NetworkEntry(
                method=request.method,
                url=request.url,
                status=response.status,
                request_body=request.post_data or "",
                response_body=body,
                request_headers=_safe_headers(await request.all_headers()),
            )
        )


# Headers the browser adds by itself or that carry credentials/session state: not shown to the model.
_HIDDEN_HEADERS = FORBIDDEN_HEADERS | {
    "user-agent",
    "referer",
    "origin",
    "accept-encoding",
    "accept-language",
    "connection",
    "host",
    "content-length",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
}


def _on_domain(url: str, domain: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == domain or host.endswith("." + domain)


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HIDDEN_HEADERS}


def registrable_domain(url: str) -> str:
    """andorinhaonline.com.br from https://www.andorinhaonline.com.br/ (good enough for .com.br)."""
    host = urlparse(url).hostname or ""
    parts = host.split(".")
    keep = 3 if len(parts) >= 3 and parts[-2] in {"com", "net", "org"} else 2
    return ".".join(parts[-keep:])


def _errors_to_model(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Browser failures (timeouts, bad selectors) go back to the model instead of ending the run."""
    if pyinspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except PlaywrightError as e:
                return f"error: {str(e).splitlines()[0]}"

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except PlaywrightError as e:
            return f"error: {str(e).splitlines()[0]}"

    return wrapper


def build_tools(session: DiscoverySession) -> list[BaseTool]:
    page = session.page

    async def summary() -> str:
        text = await page.inner_text("body")
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return f"url: {page.url}\ntitle: {await page.title()}\ntext:\n{text[:1500]}"

    @tool
    @_errors_to_model
    async def open_page(url: str) -> str:
        """Open a URL on the store's own domain and return the page's title and visible text."""
        if not session.allowed(url):
            return f"refused: {url} is outside {session.allowed_domain}"
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(PAGE_WAIT_MS)
        return await summary()

    @tool
    @_errors_to_model
    async def read_page(max_chars: int = 3000) -> str:
        """Return the current page's visible text."""
        text = re.sub(r"\n\s*\n+", "\n", await page.inner_text("body")).strip()
        return f"url: {page.url}\n{text[:max_chars]}"

    @tool
    @_errors_to_model
    async def inspect(selector: str) -> str:
        """Count elements matching a CSS selector and show the outer HTML of the first five."""
        elements = await page.locator(selector).all()
        samples = [(await e.evaluate("e => e.outerHTML"))[:400] for e in elements[:5]]
        return f"{len(elements)} match(es)\n" + "\n---\n".join(samples)

    @tool
    @_errors_to_model
    async def click(selector: str) -> str:
        """Click the first element matching a CSS or Playwright text selector (e.g. 'text=Fechar').

        Refused for anything related to login, cart, purchase or payment.
        """
        target = page.locator(selector).first
        label = f"{selector} {await target.inner_text() if await target.count() else ''}"
        if BLOCKED_ACTION.search(label):
            return "refused: discovery part 1 doesn't log in or touch the cart"
        await target.click(timeout=5000)
        await page.wait_for_timeout(PAGE_WAIT_MS)
        return await summary()

    @tool
    @_errors_to_model
    async def type_text(selector: str, text: str, press_enter: bool = True) -> str:
        """Type into an input (e.g. the search box), optionally pressing Enter. Not for logins."""
        target = page.locator(selector).first
        attributes = await target.evaluate(
            "e => (e.type || '') + ' ' + (e.name || '') + ' ' + (e.id || '')"
        )
        if re.search(r"password|senha|email|cpf|cep", attributes, re.IGNORECASE):
            return "refused: discovery doesn't fill credentials, personal data or addresses"
        await target.fill(text)
        if press_enter:
            await target.press("Enter")
        await page.wait_for_timeout(PAGE_WAIT_MS)
        return await summary()

    @tool
    @_errors_to_model
    def network_log(contains: str = "", last: int = 15) -> str:
        """List recent JSON XHR/fetch calls the page made (index, method, status, URL, body start)."""
        rows = [
            (i, e)
            for i, e in enumerate(session.network)
            if contains.lower() in (e.url + e.request_body).lower()
        ][-last:]
        if not rows:
            return "no matching JSON requests recorded yet"
        return "\n".join(
            f"[{i}] {e.method} {e.status} {e.url[:150]} body={e.request_body[:300]!r}"
            for i, e in rows
        )

    @tool
    @_errors_to_model
    def network_entry(index: int, max_chars: int = 6000) -> str:
        """Show one recorded request in full: URL, non-secret request headers, body and response."""
        if not 0 <= index < len(session.network):
            return "no such entry"
        e = session.network[index]
        return (
            f"{e.method} {e.url}\nstatus: {e.status}\n"
            f"request headers (cookies/authorization hidden): {json.dumps(e.request_headers)}\n"
            f"request body:\n{e.request_body[:4000]}\n"
            f"response (first {max_chars} chars):\n{e.response_body[:max_chars]}"
        )

    async def run_spec(spec: HttpSearch, query: str) -> tuple[str, Any]:
        payload = await fetch(session.context, spec, query)
        candidates = rank(query, parse_results(spec, payload))
        if not candidates:
            return f"{query!r}: 0 candidates", payload
        lines = [
            f"  {c.name} | brand={c.brand} | size={c.size} | price={c.price} | "
            f"unit={c.unit_of_sale.kind}{f' step={c.unit_of_sale.step_size_g}g' if c.unit_of_sale.step_size_g else ''}"
            f"{f' pack={c.unit_of_sale.pack_size}' if c.unit_of_sale.pack_size else ''} | "
            f"in_stock={c.in_stock} | {c.url}"
            for c in candidates[:5]
        ]
        return f"{query!r}: {len(candidates)} candidates, top 5:\n" + "\n".join(lines), payload

    def parse_spec(spec_yaml: str) -> HttpSearch:
        spec = HttpSearch.model_validate(yaml.safe_load(spec_yaml))
        if not session.allowed_for_search(spec.url):
            allowed = ", ".join([session.allowed_domain, *session.api_domains])
            raise ValueError(f"search URL must be on one of: {allowed}")
        if spec.page_url and not session.allowed(spec.page_url):
            raise ValueError(f"page_url must be on {session.allowed_domain}")
        return spec

    @tool
    @_errors_to_model
    async def try_search_spec(spec_yaml: str, query: str) -> str:
        """Run a draft search spec (YAML, HttpSearch schema) for one query with the real adapter."""
        try:
            summary_text, _ = await run_spec(parse_spec(spec_yaml), query)
        except (ProfileError, ValueError) as e:
            return f"error: {e}"
        return summary_text

    @tool
    @_errors_to_model
    async def submit_search_spec(spec_yaml: str) -> str:
        """Submit the final search spec. It is run for every test query; all must return results."""
        try:
            spec = parse_spec(spec_yaml)
        except ValueError as e:
            return f"error: {e}"
        reports, payloads = [], {}
        for query in session.test_queries:
            try:
                text, payload = await run_spec(spec, query)
            except ProfileError as e:
                return f"rejected: {query!r} failed: {e}"
            if text.endswith(": 0 candidates"):
                return f"rejected: {query!r} returned no candidates"
            reports.append(text)
            payloads[query] = payload
        session.accepted, session.payloads = spec, payloads
        return "accepted. Summary for the human reviewer:\n" + "\n".join(reports)

    return [
        open_page,
        read_page,
        inspect,
        click,
        type_text,
        network_log,
        network_entry,
        try_search_spec,
        submit_search_spec,
    ]


def dump_network(session: DiscoverySession) -> str:
    return json.dumps([e.__dict__ for e in session.network], ensure_ascii=False, indent=2)
