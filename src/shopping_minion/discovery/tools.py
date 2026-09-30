"""Browser tools for the discovery agent, session 1: search, no login, no cart (ADR-0006, ADR-0012).

Every tool is something a user does on the site (open a page, click, type) or an observation of what
the page showed or received. No tool sends a request of its own to the store: no HTTP client, no
`context.request`, no `fetch()` evaluated in the page, and no tool takes the URL of an API.
The guards live in code here (pure functions, unit-tested), not only in the tool descriptions.
"""

import functools
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import yaml
from langchain_core.tools import BaseTool, tool
from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError

from shopping_minion.catalog.browser_catalog import BrowserCatalog
from shopping_minion.catalog.page import ResponseLog
from shopping_minion.catalog.profile import ClickStep, FillStep, Search, SiteProfile
from shopping_minion.contracts import Candidate
from shopping_minion.workflow import SiteChangedError

MAX_TOOL_CHARS = 6000
OPEN_PAGE_TEXT_CHARS = 1500
INSPECT_HTML_CHARS = 600
BODY_PREVIEW_CHARS = 300
ACTION_TIMEOUT_MS = 10_000
PEEK_TIMEOUT_MS = 3_000
SETTLE_MS = 5_000

# Words that mean login, cart, purchase, checkout or payment (Portuguese and English).
BLOCKED_ACTION = re.compile(
    r"entrar|login|cadastr|senha|carrinho|comprar|adicionar|finalizar|checkout|pagamento|pagar|"
    r"cart|buy|add to|sign in|password",
    re.IGNORECASE,
)
# Fields that look like credentials, personal data or addresses. Short words match whole words only,
# so that "receptor" is not read as "cep".
BLOCKED_FIELD = re.compile(
    r"password|senha|e-?mail|(?<![a-z])cpf(?![a-z])|(?<![a-z])cep(?![a-z])|telefone|phone|"
    r"endere[cç]o|address",
    re.IGNORECASE,
)


# --- pure decisions ---------------------------------------------------------------------------


def registrable_domain(url_or_host: str) -> str:
    """`andorinhaonline.com.br` from `https://www.andorinhaonline.com.br/`.

    Good enough for `.com.br`-style names (a second-level `com`, `net`, `org`, `gov`, `edu`).
    """
    host = urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host
    parts = (host or "").lower().strip(".").split(".")
    keep = 3 if len(parts) >= 3 and parts[-2] in {"com", "net", "org", "gov", "edu"} else 2
    return ".".join(parts[-keep:])


def on_store_domain(url: str, base_url: str) -> bool:
    """True for http(s) URLs on the store's registrable domain or a subdomain of it."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    domain = registrable_domain(base_url)
    host = parts.hostname.lower()
    return host == domain or host.endswith("." + domain)


def click_blocked(selector: str, element_text: str = "") -> str | None:
    """The reason a click is refused, or None."""
    for what, value in (("selector", selector), ("element text", element_text)):
        found = BLOCKED_ACTION.search(value)
        if found:
            return (
                f"refused: the {what} relates to login, cart, purchase or payment "
                f"({found.group(0)!r}); discovery session 1 only searches"
            )
    return None


def field_blocked(selector: str, attributes: dict[str, str | None] | None = None) -> str | None:
    """The reason typing into a field is refused, or None.

    `attributes` are the field's type, name, id, placeholder (and similar) read from the page.
    """
    values = {"selector": selector, **(attributes or {})}
    for name, value in values.items():
        found = BLOCKED_FIELD.search(value or "")
        if found:
            return (
                f"refused: the field's {name} looks like credentials, personal data or an "
                f"address ({found.group(0)!r}); only search boxes may be typed into"
            )
    return None


def truncate(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text) - limit} more characters]"


def clean_text(text: str) -> str:
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def format_response(url: str, body: Any) -> str:
    """One logged response: the path and query (never the host), the top-level keys, a preview."""
    if isinstance(body, dict):
        keys = ", ".join(str(key) for key in list(body)[:30])
    elif isinstance(body, list):
        keys = f"(a list of {len(body)} items)"
    else:
        keys = f"(a {type(body).__name__})"
    preview = truncate(json.dumps(body, ensure_ascii=False, default=str), BODY_PREVIEW_CHARS)
    return f"{_path_and_query(url)}\n  keys: {keys}\n  body: {preview}"


def format_responses(records: list[tuple[str, Any]], contains: str = "", last: int = 10) -> str:
    """The most recent `last` responses whose path and query contain `contains`."""
    shown = [
        (url, body)
        for url, body in records
        if not contains or contains.lower() in _path_and_query(url).lower()
    ]
    if not shown:
        return "no JSON responses" + (f" matching {contains!r}" if contains else "") + "."
    shown = shown[-max(last, 1) :]
    lines = [f"{len(shown)} of {len(records)} JSON responses:"]
    lines += [f"{i}. {format_response(url, body)}" for i, (url, body) in enumerate(shown, 1)]
    return truncate("\n".join(lines))


def format_unit(candidate: Candidate) -> str:
    unit = candidate.unit_of_sale
    if unit.kind == "pack":
        return f"pack of {unit.pack_size}"
    if unit.kind == "weight_step":
        return f"weight step {unit.step_size_g:g} g"
    return "unit"


def format_candidate(candidate: Candidate) -> str:
    price = f"{candidate.price:.2f}" if candidate.price is not None else "no price"
    stock = "in stock" if candidate.in_stock else "out of stock"
    return (
        f"{candidate.name} | {candidate.brand or 'no brand'} | {price} | "
        f"{format_unit(candidate)} | {stock} | {candidate.url}"
    )


def format_candidates(candidates: list[Candidate], shown: int = 5) -> str:
    lines = [f"{len(candidates)} candidates"]
    lines += [format_candidate(c) for c in candidates[:shown]]
    return "\n".join(lines)


def _validation_text(error: ValidationError) -> str:
    lines = []
    for item in error.errors()[:8]:
        where = ".".join(str(part) for part in item["loc"]) or "(section)"
        lines.append(f"{where}: {item['msg']}")
    return "; ".join(lines)


def check_draft_steps(search: Search) -> str | None:
    """The same guards as the click and type_text tools, applied to a draft's steps."""
    for step in search.steps:
        if isinstance(step, ClickStep):
            reason = click_blocked(step.click)
            if reason:
                return f"step click {step.click!r}: {reason}"
        elif isinstance(step, FillStep):
            if step.env is not None:
                return f"step fill {step.fill!r}: {step.env} is a credential; session 1 has none"
            reason = field_blocked(step.fill)
            if reason:
                return f"step fill {step.fill!r}: {reason}"
    return None


def parse_search_section(section_yaml: str, store: str, base_url: str) -> SiteProfile | str:
    """Build a SiteProfile from a draft `search` section, or return `error: ...` text."""
    try:
        data = yaml.safe_load(section_yaml)
    except yaml.YAMLError as error:
        return f"error: the section is not valid YAML: {str(error).splitlines()[0]}"
    if not isinstance(data, dict):
        return "error: the section must be a YAML mapping with 'steps' and 'results'"
    try:
        search = Search.model_validate(data)
        profile = SiteProfile(store=store, version=1, base_url=base_url, search=search)
    except ValidationError as error:
        return f"error: invalid search section: {_validation_text(error)}"
    problem = check_draft_steps(search)
    return f"error: {problem}" if problem else profile


# --- the session ------------------------------------------------------------------------------


@dataclass
class DiscoverySession:
    context: BrowserContext
    page: Page
    store: str
    base_url: str
    test_queries: list[str]
    log: ResponseLog = field(default_factory=ResponseLog)
    accepted: Search | None = None
    # What the accepted section returned for each test query (public catalog data, kept as fixtures).
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page is not None:
            self.log.attach(self.page)


def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return lines[0] if lines else type(error).__name__


def _errors_to_model(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """A browser failure (timeout, bad selector) goes back to the model as text; it doesn't raise."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await fn(*args, **kwargs)
        except PlaywrightError as error:
            return truncate(f"error: {_first_line(error)}", 500)

    return wrapper


# --- the tools --------------------------------------------------------------------------------


def build_tools(session: DiscoverySession) -> list[BaseTool]:
    page = session.page

    async def settle() -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
        except PlaywrightError:
            pass  # a page that never goes idle is still readable
        await session.log.settle()

    async def visible_text() -> str:
        return clean_text(await page.inner_text("body", timeout=ACTION_TIMEOUT_MS))

    async def summary() -> str:
        text = truncate(await visible_text(), OPEN_PAGE_TEXT_CHARS)
        return truncate(f"title: {await page.title()}\nurl: {page.url}\ntext:\n{text}")

    async def leave_if_off_store() -> str | None:
        if on_store_domain(page.url, session.base_url):
            return None
        left = urlsplit(page.url).hostname
        await page.go_back()
        return f"error: that led to {left}, outside the store's domain; went back"

    @tool
    @_errors_to_model
    async def open_page(url: str) -> str:
        """Open a page of the store (its own domain only) and return its title, URL and first text."""
        if not on_store_domain(url, session.base_url):
            return (
                f"refused: only pages on {registrable_domain(session.base_url)} "
                "(and its subdomains) may be opened"
            )
        session.log.clear()
        await page.goto(url, wait_until="domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        await settle()
        return await leave_if_off_store() or await summary()

    @tool
    @_errors_to_model
    async def read_page(max_chars: int = 3000) -> str:
        """Return the current page's visible text, at most max_chars characters."""
        return truncate(await visible_text(), min(max(max_chars, 100), MAX_TOOL_CHARS))

    @tool
    @_errors_to_model
    async def inspect(selector: str) -> str:
        """Count the elements matching a selector and show the outer HTML of the first five."""
        locator = page.locator(selector)
        count = await locator.count()
        lines = [f"{count} elements match {selector!r}"]
        for i in range(min(count, 5)):
            html = await locator.nth(i).evaluate("e => e.outerHTML")
            lines.append(f"[{i}] {truncate(html, INSPECT_HTML_CHARS)}")
        return truncate("\n".join(lines))

    @tool
    @_errors_to_model
    async def click(selector: str) -> str:
        """Click an element as a user would, then return the page summary. Not for login or cart."""
        reason = click_blocked(selector)
        if reason:
            return reason
        target = page.locator(selector).first
        element_text = await target.evaluate(
            "e => [e.innerText, e.getAttribute('aria-label'), e.getAttribute('title'), e.value]"
            ".filter(Boolean).join(' ')",
            timeout=PEEK_TIMEOUT_MS,
        )
        reason = click_blocked(selector, element_text)
        if reason:
            return reason
        session.log.clear()
        await target.click(timeout=ACTION_TIMEOUT_MS)
        await settle()
        return await leave_if_off_store() or await summary()

    @tool
    @_errors_to_model
    async def type_text(selector: str, text: str, press_enter: bool = True) -> str:
        """Type text into a search field, optionally press Enter, then return the page summary."""
        reason = field_blocked(selector)
        if reason:
            return reason
        target = page.locator(selector).first
        attributes = await target.evaluate(
            "e => ({type: e.type, name: e.name, id: e.id, placeholder: e.placeholder,"
            " autocomplete: e.autocomplete, label: e.getAttribute('aria-label')})",
            timeout=PEEK_TIMEOUT_MS,
        )
        reason = field_blocked(selector, attributes)
        if reason:
            return reason
        session.log.clear()
        await target.fill(text, timeout=ACTION_TIMEOUT_MS)
        if press_enter:
            await target.press("Enter", timeout=ACTION_TIMEOUT_MS)
        await settle()
        return await leave_if_off_store() or await summary()

    @tool
    @_errors_to_model
    async def page_responses(contains: str = "", last: int = 10) -> str:
        """List the JSON responses the page received since the last action (observation only)."""
        await session.log.settle()
        return format_responses(session.log.records(), contains, last)

    async def run_draft(
        section_yaml: str, query: str
    ) -> tuple[list[Candidate], None] | tuple[None, str]:
        profile = parse_search_section(section_yaml, session.store, session.base_url)
        if isinstance(profile, str):
            return None, profile
        catalog = BrowserCatalog(profile, session.context)
        try:
            return await catalog.search(query), None
        except (SiteChangedError, ValueError) as error:
            return None, f"error: {_first_line(error)}"
        finally:
            await catalog.close()

    @tool
    @_errors_to_model
    async def try_search(section_yaml: str, query: str) -> str:
        """Run a draft `search` section (YAML) for one query on a separate page; show the results."""
        candidates, error = await run_draft(section_yaml, query)
        if error is not None:
            return truncate(error, 1500)
        assert candidates is not None
        return truncate(format_candidates(candidates))

    @tool
    @_errors_to_model
    async def submit_search(section_yaml: str) -> str:
        """Run a draft `search` section for every test query; accepted only if all return results."""
        lines = []
        found: dict[str, list[Candidate]] = {}
        for query in session.test_queries:
            candidates, error = await run_draft(section_yaml, query)
            if error is not None:
                return truncate(f"rejected: query {query!r}: {error}", 1500)
            assert candidates is not None
            if not candidates:
                return f"rejected: query {query!r}: no candidates"
            found[query] = candidates
            lines.append(f"query {query!r}: {format_candidates(candidates, shown=2)}")
        profile = parse_search_section(section_yaml, session.store, session.base_url)
        assert isinstance(profile, SiteProfile)
        session.accepted = profile.search
        session.candidates = found
        return truncate("accepted\n" + "\n".join(lines))

    return [
        open_page,
        read_page,
        inspect,
        click,
        type_text,
        page_responses,
        try_search,
        submit_search,
    ]
