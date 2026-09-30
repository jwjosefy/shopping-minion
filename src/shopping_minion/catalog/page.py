"""Helpers that drive a store's page the way a user does (ADR-0012).

Shared by the catalog, the cart executor and the discovery tools. Nothing here sends a request of
its own: it opens pages, types, clicks, waits, and reads what the page shows or received.
The functions that touch a `Page` are thin; the decisions are pure functions (`render`,
`ResponseLog.find`, `select_candidates`, `check_step_allowed`) that are unit-tested.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote as _url_quote
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, Response

from shopping_minion.catalog.mapping import FieldMap, UnitRule
from shopping_minion.catalog.profile import (
    PLACEHOLDERS,
    CartRead,
    ClickStep,
    FillStep,
    OpenStep,
    PressStep,
    SearchResults,
    Step,
    WaitStep,
)
from shopping_minion.catalog.reading import (
    ReadingError,
    ReadLine,
    candidates_from_html,
    candidates_from_items,
    candidates_from_response,
    lines_from_html,
    lines_from_items,
    lines_from_response,
)
from shopping_minion.contracts import Candidate
from shopping_minion.workflow import SiteChangedError

DEFAULT_TIMEOUT_MS = 15000
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_SKIPPED_RESOURCES = frozenset({"image", "font", "stylesheet", "media"})


# --- templates --------------------------------------------------------------------------------


def render(template: str, values: Mapping[str, str], *, quote: bool) -> str:
    """Replace `{base_url}`, `{query}`, `{name}`, `{quantity}`.

    With `quote=True` (URLs) every value but `base_url` is URL-quoted; with `quote=False` (text
    typed into a field) values go in as they are. An unknown or missing placeholder is a ValueError.
    """

    def replace(match: re.Match[str]) -> str:
        name = match[1]
        if name not in PLACEHOLDERS:
            allowed = ", ".join("{" + p + "}" for p in sorted(PLACEHOLDERS))
            raise ValueError(f"unknown placeholder {{{name}}} in {template!r}; allowed: {allowed}")
        if name not in values:
            raise ValueError(f"no value given for placeholder {{{name}}} in {template!r}")
        value = values[name]
        return _url_quote(value, safe="") if quote and name != "base_url" else value

    return _PLACEHOLDER_RE.sub(replace, template)


# --- steps ------------------------------------------------------------------------------------


class ForbiddenStepError(RuntimeError):
    """A step would click or open something the profile says is never touched (checkout)."""


def check_step_allowed(step: Step, values: Mapping[str, str], never: Sequence[str]) -> None:
    """Raise ForbiddenStepError for a click on a `never` selector or an open of a `never` URL."""
    if isinstance(step, ClickStep) and step.click in never:
        raise ForbiddenStepError(f"click {step.click!r} is a checkout marker and is never clicked")
    if isinstance(step, OpenStep):
        url = render(step.open, values, quote=True)
        hit = next((marker for marker in never if marker and marker in url), None)
        if hit is not None:
            raise ForbiddenStepError(f"open {url!r} contains the checkout marker {hit!r}")


def _fill_text(step: FillStep, values: Mapping[str, str]) -> str:
    if step.env is not None:
        text = os.environ.get(step.env)
        if not text:
            raise RuntimeError(f"environment variable {step.env} is not set or is empty")
        return text
    assert step.value is not None  # the profile schema guarantees value or env
    return render(step.value, values, quote=False)


async def run_steps(
    page: Page,
    steps: Sequence[Step],
    values: Mapping[str, str],
    *,
    never: Sequence[str] = (),
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    root: Locator | None = None,
) -> None:
    """Run profile steps in order (open, fill, press, click, wait_for).

    Every step is checked against `never` first, so a forbidden step stops the run before
    anything happens. A Playwright timeout or missing element raises SiteChangedError.

    With `root` (e.g. one product card), `fill`, `press` (with a field), `click` and `wait_for`
    act on elements inside it. `open` is refused with ValueError: a page can't be opened inside a
    card. A `press` without a field goes to the keyboard, as without `root`.
    """
    opened = next((step for step in steps if isinstance(step, OpenStep)), None)
    if root is not None and opened is not None:
        raise ValueError(
            f"open {opened.open!r} can't run inside a locator: a page can't be opened inside a card"
        )
    for step in steps:
        check_step_allowed(step, values, never)
    for step in steps:
        await _run_step(page, step, values, timeout_ms, root)


async def _run_step(
    page: Page,
    step: Step,
    values: Mapping[str, str],
    timeout_ms: int,
    root: Locator | None = None,
) -> None:
    kind, target = "step", ""
    try:
        if isinstance(step, OpenStep):
            kind = "open"
            target = url = render(step.open, values, quote=True)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        elif isinstance(step, FillStep):
            kind, target = "fill", step.fill
            text = _fill_text(step, values)  # a RuntimeError for an unset variable passes through
            if root is not None:
                await root.locator(step.fill).fill(text, timeout=timeout_ms)
            else:
                await page.fill(step.fill, text, timeout=timeout_ms)
        elif isinstance(step, PressStep):
            kind, target = "press", step.field or step.press
            if step.field and root is not None:
                await root.locator(step.field).press(step.press, timeout=timeout_ms)
            elif step.field:
                await page.press(step.field, step.press, timeout=timeout_ms)
            else:
                await page.keyboard.press(step.press)
        elif isinstance(step, ClickStep):
            kind, target = "click", step.click
            if root is not None:
                await root.locator(step.click).click(timeout=timeout_ms)
            else:
                await page.click(step.click, timeout=timeout_ms)
        elif isinstance(step, WaitStep):
            kind, target = "wait_for", step.wait_for
            if root is not None:
                await root.locator(step.wait_for).first.wait_for(timeout=timeout_ms)
            else:
                await page.wait_for_selector(step.wait_for, timeout=timeout_ms)
    except PlaywrightError as error:
        # Names the step and its selector or URL, never the typed value or Playwright's text.
        raise SiteChangedError(
            f"step {kind} {target!r} failed ({type(error).__name__}): the site may have changed"
        ) from error


# --- responses the page received --------------------------------------------------------------


class ResponseLog:
    """The JSON responses a page received (observed, never requested)."""

    def __init__(self, records: Sequence[tuple[str, Any]] = ()) -> None:
        self._records: list[tuple[str, Any]] = list(records)
        self._pending: set[asyncio.Future[None]] = set()

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        task = asyncio.ensure_future(self._record(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _record(self, response: Response) -> None:
        try:
            if response.request.resource_type in _SKIPPED_RESOURCES:
                return
            body = await response.json()
        except Exception:  # noqa: BLE001 - not JSON, no body (redirect) or page gone: not recorded
            return
        self.add(response.url, body)

    def add(self, url: str, body: Any) -> None:
        self._records.append((url, body))

    async def settle(self) -> None:
        """Wait until the bodies of responses that already arrived have been read."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def clear(self) -> None:
        for task in self._pending:
            task.cancel()
        self._pending.clear()
        self._records.clear()

    def find(self, url_matches: str) -> Any | None:
        """Body of the most recent response whose URL path (plus query) matches the regex."""
        pattern = re.compile(url_matches)
        for url, body in reversed(self._records):
            parts = urlsplit(url)
            target = parts.path + (f"?{parts.query}" if parts.query else "")
            if pattern.search(target):
                return body
        return None


# --- reading what the page shows --------------------------------------------------------------


@dataclass(frozen=True)
class ScriptOutcome:
    """What running `from_script` gave: a list, or the reason it failed."""

    items: list[Any] | None = None
    error: str | None = None


def select_candidates(
    sources: SearchResults | CartRead,
    *,
    base_url: str,
    response_body: Any | None,
    html: str | None,
    script: ScriptOutcome | None,
) -> list[Candidate]:
    """Pure decision over what was obtained: the first source that works, else `[]` or an error.

    A source works when it produced a list, even an empty one, but an empty list is returned only
    after the remaining sources were tried and none returned items. If every configured source
    failed, SiteChangedError says why for each.
    """
    attempts: list[tuple[str, list[Candidate] | str]] = []
    if sources.from_response is not None:
        attempts.append(("from_response", _from_response(sources, response_body, base_url)))
    if sources.from_dom is not None:
        attempts.append(("from_dom", _from_dom(sources, html, base_url)))
    if sources.from_script is not None:
        attempts.append(("from_script", _from_script(sources, script, base_url)))

    return _choose(attempts)


def _choose[T](attempts: list[tuple[str, list[T] | str]]) -> list[T]:
    worked = [result for _, result in attempts if not isinstance(result, str)]
    for result in worked:
        if isinstance(result, list) and result:
            return result
    if worked:
        return []
    reasons = "; ".join(f"{name}: {result}" for name, result in attempts)
    raise SiteChangedError(f"no way of reading the results worked ({reasons})")


def _attempt[T](read: Callable[[], list[T]]) -> list[T] | str:
    try:
        return read()
    except ReadingError as error:
        return str(error)


def _from_response(
    sources: SearchResults | CartRead, body: Any | None, base_url: str
) -> list[Candidate] | str:
    source = sources.from_response
    assert source is not None
    if body is None:
        return "no response matching url_matches was received"
    return _attempt(lambda: candidates_from_response(source, body, base_url=base_url))


def _from_dom(
    sources: SearchResults | CartRead, html: str | None, base_url: str
) -> list[Candidate] | str:
    source = sources.from_dom
    assert source is not None
    if html is None:
        return "the page's HTML could not be read"
    return _attempt(lambda: candidates_from_html(source, html, base_url=base_url))


def _from_script(
    sources: SearchResults | CartRead, script: ScriptOutcome | None, base_url: str
) -> list[Candidate] | str:
    source = sources.from_script
    assert source is not None
    if script is None or script.items is None:
        return script.error if script and script.error else "the script returned no list"
    items = script.items
    fields: FieldMap = source.fields
    rule: UnitRule = source.unit_of_sale
    return _attempt(lambda: candidates_from_items(fields, rule, items, base_url=base_url))


async def _observe(
    page: Page,
    sources: SearchResults | CartRead,
    log: ResponseLog,
    timeout_ms: int,
) -> tuple[Any | None, str | None, ScriptOutcome | None]:
    """Wait for `wait_for`, then gather what each configured source needs from the page."""
    if sources.wait_for:
        try:
            await page.wait_for_selector(sources.wait_for, timeout=timeout_ms)
        except PlaywrightError as error:
            raise SiteChangedError(
                f"wait_for {sources.wait_for!r} did not appear ({type(error).__name__}): "
                "the site may have changed"
            ) from error

    await log.settle()
    body = log.find(sources.from_response.url_matches) if sources.from_response else None
    html = await _page_html(page) if sources.from_dom else None
    script = await _run_script(page, sources.from_script.script) if sources.from_script else None
    return body, html, script


async def read_candidates(
    page: Page,
    sources: SearchResults | CartRead,
    *,
    base_url: str,
    log: ResponseLog,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> list[Candidate]:
    """Wait for `wait_for`, then read the page with the sources in order (see select_candidates)."""
    body, html, script = await _observe(page, sources, log, timeout_ms)
    return select_candidates(
        sources, base_url=base_url, response_body=body, html=html, script=script
    )


def select_lines(
    sources: CartRead,
    *,
    base_url: str,
    response_body: Any | None,
    html: str | None,
    script: ScriptOutcome | None,
) -> list[ReadLine]:
    """Like `select_candidates`, for the cart: each line keeps the quantity read from its item."""
    quantity = sources.quantity
    attempts: list[tuple[str, list[ReadLine] | str]] = []
    if sources.from_response is not None:
        response = sources.from_response
        if response_body is None:
            attempts.append(("from_response", "no response matching url_matches was received"))
        else:
            attempts.append(
                (
                    "from_response",
                    _attempt(
                        lambda: lines_from_response(
                            response, response_body, base_url=base_url, quantity=quantity
                        )
                    ),
                )
            )
    if sources.from_dom is not None:
        dom = sources.from_dom
        if html is None:
            attempts.append(("from_dom", "the page's HTML could not be read"))
        else:
            attempts.append(
                (
                    "from_dom",
                    _attempt(
                        lambda: lines_from_html(dom, html, base_url=base_url, quantity=quantity)
                    ),
                )
            )
    if sources.from_script is not None:
        source = sources.from_script
        if script is None or script.items is None:
            reason = script.error if script and script.error else "the script returned no list"
            attempts.append(("from_script", reason))
        else:
            items = script.items
            attempts.append(
                (
                    "from_script",
                    _attempt(
                        lambda: lines_from_items(
                            source.fields,
                            source.unit_of_sale,
                            items,
                            base_url=base_url,
                            quantity=quantity,
                        )
                    ),
                )
            )
    return _choose(attempts)


async def read_cart_lines(
    page: Page,
    sources: CartRead,
    *,
    base_url: str,
    log: ResponseLog,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    empty_is_ok: bool = False,
) -> list[ReadLine]:
    """Wait for `wait_for`, then read the cart's lines (candidate and quantity) like read_candidates.

    With `empty_is_ok`, a cart that shows nothing to read (its `wait_for` never appears, or no
    source produces anything) is `[]` instead of SiteChangedError: an empty cart may show none of
    the elements a filled one does. Use it only where an empty cart is a normal answer.
    """
    try:
        body, html, script = await _observe(page, sources, log, timeout_ms)
        return select_lines(
            sources, base_url=base_url, response_body=body, html=html, script=script
        )
    except SiteChangedError:
        if empty_is_ok:
            return []
        raise


async def _page_html(page: Page) -> str | None:
    try:
        return await page.content()
    except PlaywrightError:
        return None


async def _run_script(page: Page, script: str) -> ScriptOutcome:
    try:
        result = await page.evaluate(script)
    except PlaywrightError as error:
        return ScriptOutcome(error=f"the script failed ({type(error).__name__})")
    if not isinstance(result, list):
        return ScriptOutcome(error="the script did not return a list")
    return ScriptOutcome(items=result)
