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
from playwright.async_api import Page, Response

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
    candidates_from_html,
    candidates_from_items,
    candidates_from_response,
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
) -> None:
    """Run profile steps in order (open, fill, press, click, wait_for).

    Every step is checked against `never` first, so a forbidden step stops the run before
    anything happens. A Playwright timeout or missing element raises SiteChangedError.
    """
    for step in steps:
        check_step_allowed(step, values, never)
    for step in steps:
        await _run_step(page, step, values, timeout_ms)


async def _run_step(page: Page, step: Step, values: Mapping[str, str], timeout_ms: int) -> None:
    kind, target = "step", ""
    try:
        if isinstance(step, OpenStep):
            kind = "open"
            target = url = render(step.open, values, quote=True)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        elif isinstance(step, FillStep):
            kind, target = "fill", step.fill
            text = _fill_text(step, values)  # a RuntimeError for an unset variable passes through
            await page.fill(step.fill, text, timeout=timeout_ms)
        elif isinstance(step, PressStep):
            kind, target = "press", step.field or step.press
            if step.field:
                await page.press(step.field, step.press, timeout=timeout_ms)
            else:
                await page.keyboard.press(step.press)
        elif isinstance(step, ClickStep):
            kind, target = "click", step.click
            await page.click(step.click, timeout=timeout_ms)
        elif isinstance(step, WaitStep):
            kind, target = "wait_for", step.wait_for
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

    worked = [result for _, result in attempts if not isinstance(result, str)]
    for result in worked:
        if isinstance(result, list) and result:
            return result
    if worked:
        return []
    reasons = "; ".join(f"{name}: {result}" for name, result in attempts)
    raise SiteChangedError(f"no way of reading the results worked ({reasons})")


def _attempt(read: Callable[[], list[Candidate]]) -> list[Candidate] | str:
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


async def read_candidates(
    page: Page,
    sources: SearchResults | CartRead,
    *,
    base_url: str,
    log: ResponseLog,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> list[Candidate]:
    """Wait for `wait_for`, then read the page with the sources in order (see select_candidates)."""
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
    return select_candidates(
        sources, base_url=base_url, response_body=body, html=html, script=script
    )


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
