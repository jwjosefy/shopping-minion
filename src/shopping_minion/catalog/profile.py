"""Site profile: what a user does on a store's site, and where to read what the site shows.

`profiles/<store>/profile.yaml`, validated here (ADR-0006, ADR-0012). A profile describes user
steps and where to read results. It never describes an endpoint to call, a host, an id or a request
parameter, and it never holds a credential. No browser code and no store access lives here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from shopping_minion.catalog.mapping import DomSource, FieldMap, ResponseSource, UnitRule

DEFAULT_PROFILES_DIR = Path("profiles")

PLACEHOLDERS = frozenset({"base_url", "query", "name", "quantity"})
ENV_VARS = ("STORE_EMAIL", "STORE_PASSWORD")
SECRET_WORDS = ("cookie", "token", "authorization", "password")

_STORE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_DIGIT_RUN_RE = re.compile(r"\d{3,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S|authorization\s*:")
# A script reads what the app already has. It may not send requests of its own (ADR-0012).
_SCRIPT_REQUEST_RE = re.compile(
    r"\bfetch\s*\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource|importScripts|://"
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- steps ------------------------------------------------------------------------------------


class OpenStep(_Model):
    """Open a page of the store. `open` is a URL template, e.g. `{base_url}busca/{query}`."""

    open: str


class FillStep(_Model):
    """Type into the field found by `fill` (a selector).

    The text comes from `value` (a template, e.g. `{query}`) or from the environment variable
    named in `env`. Never both, and never a secret written out.
    """

    fill: str
    value: str | None = None
    env: Literal["STORE_EMAIL", "STORE_PASSWORD"] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> FillStep:
        if self.value is not None and self.env is not None:
            raise ValueError(
                "a fill step takes its text from either 'value' or 'env', not both "
                "(a secret is never written in the profile)"
            )
        if self.value is None and self.env is None:
            raise ValueError(
                "a fill step needs 'value' (a template) or 'env' (STORE_EMAIL or STORE_PASSWORD)"
            )
        return self


class PressStep(_Model):
    """Press a key (e.g. `Enter`), on the field found by `field` if given (not `on`: YAML reads it as true)."""

    press: str
    field: str | None = None


class ClickStep(_Model):
    """Click the element found by a selector (or `text=...`)."""

    click: str


class WaitStep(_Model):
    """Wait until a selector, or `text=...`, is present."""

    wait_for: str


_STEP_KEYS: dict[str, type[_Model]] = {
    "open": OpenStep,
    "fill": FillStep,
    "press": PressStep,
    "click": ClickStep,
    "wait_for": WaitStep,
}


def _step_tag(value: Any) -> str | None:
    if isinstance(value, dict):
        found = [key for key in _STEP_KEYS if key in value]
        return found[0] if len(found) == 1 else None
    for key, cls in _STEP_KEYS.items():
        if isinstance(value, cls):
            return key
    return None


Step = Annotated[
    Annotated[OpenStep, Tag("open")]
    | Annotated[FillStep, Tag("fill")]
    | Annotated[PressStep, Tag("press")]
    | Annotated[ClickStep, Tag("click")]
    | Annotated[WaitStep, Tag("wait_for")],
    Discriminator(
        _step_tag,
        custom_error_type="invalid_step",
        custom_error_message="a step must have exactly one of: open, fill, press, click, wait_for",
    ),
]


# --- reading results --------------------------------------------------------------------------


class ScriptSource(_Model):
    """Last resort: a JavaScript expression that returns the list of items from the app's state.

    It only reads; it can't send requests (ADR-0012). Its items are mapped with `fields`.
    """

    script: str
    fields: FieldMap
    unit_of_sale: UnitRule = UnitRule()

    @field_validator("script")
    @classmethod
    def _reads_only(cls, value: str) -> str:
        found = _SCRIPT_REQUEST_RE.search(value)
        if found:
            raise ValueError(
                "a script may only read what the page already has, and may not send requests "
                f"or contain a URL (found {found.group(0)!r}; ADR-0012)"
            )
        return value


class _Sources(_Model):
    """The three ways to read, tried in this order: response, DOM, script."""

    from_response: ResponseSource | None = None
    from_dom: DomSource | None = None
    from_script: ScriptSource | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> _Sources:
        if not (self.from_response or self.from_dom or self.from_script):
            raise ValueError(
                "at least one source is required: from_response, from_dom or from_script"
            )
        return self


class SearchResults(_Sources):
    wait_for: str


class CartRead(_Sources):
    wait_for: str | None = None


class Search(_Model):
    steps: Annotated[list[Step], Field(min_length=1)]
    results: SearchResults


class Login(_Model):
    steps: Annotated[list[Step], Field(min_length=1)]
    logged_in_when: str  # selector or text present when logged in


class StepperQuantity(_Model):
    """Set the quantity by clicking a stepper's button: N-1 clicks after adding one unit."""

    kind: Literal["stepper"] = "stepper"
    click: str


class FieldQuantity(_Model):
    """Set the quantity by typing `{quantity}` into a field, then running `then` (e.g. Enter)."""

    kind: Literal["field"] = "field"
    fill: str
    then: list[Step] = []


Quantity = Annotated[StepperQuantity | FieldQuantity, Field(discriminator="kind")]


class Cart(_Model):
    add: Annotated[list[Step], Field(min_length=1)]  # adds one unit of a product on the page
    quantity: Quantity
    read: CartRead
    remove: Annotated[list[Step], Field(min_length=1)]
    checkout_markers: Annotated[list[str], Field(min_length=1)]  # never clicked or opened


# --- the profile ------------------------------------------------------------------------------


class SiteProfile(_Model):
    store: str
    version: int = Field(ge=1)
    base_url: str
    search: Search | None = None
    login: Login | None = None
    cart: Cart | None = None
    notes: str | None = None

    @field_validator("store")
    @classmethod
    def _valid_store(cls, value: str) -> str:
        return check_store_name(value)

    @field_validator("base_url")
    @classmethod
    def _valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an http(s) URL with a host")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("base_url must have no query, fragment or credentials")
        if not value.endswith("/"):
            raise ValueError("base_url must end with '/' so that '{base_url}path' is unambiguous")
        return value

    @model_validator(mode="after")
    def _check_rules(self) -> SiteProfile:
        steps = self._all_steps()
        for step in steps:
            if isinstance(step, OpenStep):
                self._check_open(step.open)
            elif isinstance(step, FillStep):
                _check_fill(step)
        for source in self._all_sources():
            if isinstance(source, ResponseSource):
                _check_url_matches(source.url_matches)
            self._check_field_url(source.fields.url)
        if self.cart:
            self._check_checkout_markers(steps, self.cart)
        _check_no_secret_text(self.model_dump(mode="json"), "profile")
        return self

    def _all_steps(self) -> list[Any]:
        found: list[Any] = []
        if self.search:
            found += self.search.steps
        if self.login:
            found += self.login.steps
        if self.cart:
            found += self.cart.add + self.cart.remove
            if isinstance(self.cart.quantity, FieldQuantity):
                found += self.cart.quantity.then
        return found

    def _all_sources(self) -> list[Any]:
        containers: list[_Sources] = []
        if self.search:
            containers.append(self.search.results)
        if self.cart:
            containers.append(self.cart.read)
        return [
            source
            for container in containers
            for source in (container.from_response, container.from_dom, container.from_script)
            if source is not None
        ]

    def _check_open(self, template: str) -> None:
        _check_placeholders(template, "open URL")
        self._check_under_base(template)

    def _check_under_base(self, template: str) -> None:
        url = template.replace("{base_url}", self.base_url)
        base, target = urlsplit(self.base_url), urlsplit(url)
        same_site = (
            target.scheme == base.scheme
            and target.netloc == base.netloc
            and target.path.startswith(base.path)
        )
        if not same_site:
            raise ValueError(
                f"open URL {template!r} must be a page under base_url {self.base_url!r}: "
                "start it with '{base_url}'; no other URL may be opened"
            )

    def _check_field_url(self, template: str) -> None:
        """`FieldMap.url` may only point to a page of the store (mapping.py's contract)."""
        if template.startswith("/") and not template.startswith("//"):
            return  # a path on the store's own site
        self._check_under_base(template)  # its {placeholders} are item paths, not ours

    @staticmethod
    def _check_checkout_markers(steps: list[Any], cart: Cart) -> None:
        markers = set(cart.checkout_markers)
        for step in steps:
            target = None
            if isinstance(step, ClickStep):
                target = step.click
            elif isinstance(step, OpenStep):
                target = step.open
            if target in markers:
                raise ValueError(
                    f"step {target!r} is listed in checkout_markers: a step may never "
                    "click or open a checkout marker"
                )


def check_store_name(store: str) -> str:
    if not _STORE_RE.fullmatch(store):
        raise ValueError(
            f"store name {store!r} must be lowercase letters, digits and hyphens "
            "(e.g. 'examplestore', 'my-store')"
        )
    return store


def _check_placeholders(template: str, what: str) -> None:
    for name in _PLACEHOLDER_RE.findall(template):
        if name not in PLACEHOLDERS:
            allowed = ", ".join("{" + p + "}" for p in sorted(PLACEHOLDERS))
            raise ValueError(
                f"unknown placeholder {{{name}}} in {what} {template!r}; allowed: {allowed}"
            )


def _check_fill(step: FillStep) -> None:
    if step.value is None:
        return
    _check_placeholders(step.value, "fill value")
    if any(env in step.value for env in ENV_VARS):
        raise ValueError(
            f"fill value {step.value!r} contains an environment variable name as text; "
            "use the 'env' field instead"
        )
    is_template = bool(_PLACEHOLDER_RE.search(step.value))
    hit = next((word for word in SECRET_WORDS if word in step.fill.lower()), None)
    if hit and not is_template:
        raise ValueError(
            f"fill field {step.fill!r} looks like a secret field ({hit}) but has a literal "
            "'value'; a secret is never written in the profile, use 'env'"
        )


def _check_url_matches(pattern: str) -> None:
    if "://" in pattern:
        raise ValueError(
            f"url_matches {pattern!r} contains a scheme or host ('://'); it may only recognise "
            "a response by its path"
        )
    if "=" in pattern:
        raise ValueError(
            f"url_matches {pattern!r} contains '=' (a query value to send); it may only "
            "recognise a response by its path"
        )
    if _DIGIT_RUN_RE.search(pattern):
        raise ValueError(
            f"url_matches {pattern!r} contains a run of three or more digits (a store or "
            "tenant id); it may only recognise a response by its path"
        )
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(
            f"url_matches {pattern!r} is not a valid regular expression: {error}"
        ) from error


def _check_no_secret_text(node: Any, where: str) -> None:
    """Reject env-var names and bearer-like text anywhere except the fill step's `env`."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key != "env":
                _check_no_secret_text(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _check_no_secret_text(value, f"{where}[{index}]")
    elif isinstance(node, str):
        if any(env in node for env in ENV_VARS):
            raise ValueError(
                f"{where} contains an environment variable name as text; "
                "only a fill step's 'env' may name one"
            )
        if _BEARER_RE.search(node):
            raise ValueError(f"{where} looks like an authorization header or bearer token")


# --- files ------------------------------------------------------------------------------------


def profile_path(store: str, base_dir: Path = DEFAULT_PROFILES_DIR) -> Path:
    check_store_name(store)
    return Path(base_dir) / store / "profile.yaml"


def load_profile(store: str, base_dir: Path = DEFAULT_PROFILES_DIR) -> SiteProfile:
    path = profile_path(store, base_dir)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        profile = SiteProfile.model_validate(data)
    except ValidationError as error:
        raise ValueError(f"invalid site profile {path}:\n{error}") from error
    if profile.store != store:
        raise ValueError(f"{path} is for store {profile.store!r}, not {store!r}")
    return profile


def save_profile(profile: SiteProfile, base_dir: Path = DEFAULT_PROFILES_DIR) -> Path:
    path = profile_path(profile.store, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(mode="json", exclude_none=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )
    return path
