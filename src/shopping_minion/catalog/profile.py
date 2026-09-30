"""Site profile: what discovery learned about a store, executed by the generic adapter (ADR-0006).

Only the search section is typed so far (M2). Login, cart and history get their own models when
discovery covers them (M4). Search is described as an HTTP call; browser-step search can be added
as another `kind` when a store needs it (ADR-0007).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROFILES_DIR = Path("profiles")

# Profiles are committed (ADR-0006), so they must never carry credentials or session state.
FORBIDDEN_HEADERS = {"cookie", "authorization", "proxy-authorization", "x-api-key", "x-auth-token"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Model):
    """True when the value at `path` equals one of `equals`, or matches the regex `matches`."""

    path: str
    equals: list[str | int | float | bool] | None = None
    matches: str | None = None


class ValueSpec(_Model):
    """A number read from `path` (optionally via `regex`, first group) and multiplied by `scale`.

    `path: name` with a regex reads from the product name, e.g. pack size from "12 rolos".
    `constant` is used when the path is absent or doesn't resolve.
    """

    path: str | None = None
    regex: str | None = None
    scale: float = 1.0
    constant: float | None = None


class UnitRule(_Model):
    """How to derive a product's unit of sale (ADR-0004) from a search result."""

    weight_when: Condition | None = None
    step_g: ValueSpec | None = None
    pack_size: ValueSpec | None = None


class FieldMap(_Model):
    """Where each Candidate field lives inside one result item (dotted paths, `[n]` for lists).

    `url` is a template whose `{...}` placeholders are paths, e.g. "https://x.com/p/{slug}".
    """

    id: str
    name: str
    url: str
    brand: str | None = None
    size: str | None = None
    price: str | None = None
    in_stock: Condition | None = None


class HttpSearch(_Model):
    kind: Literal["http"] = "http"
    # "request": Playwright's request context. "page": fetch() from inside a page of the store, for
    # APIs that bot protection only lets a real page call (CORS + Cloudflare); needs `page_url`.
    transport: Literal["request", "page"] = "request"
    page_url: str | None = None
    method: Literal["GET", "POST"] = "GET"
    url: str = Field(description="May contain {query} (URL-encoded) and {limit}")
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = Field(default=None, description="JSON body; strings may contain {query}, {limit}")
    results_path: str = Field(description="Dotted path to the list of result items")
    fields: FieldMap
    unit_of_sale: UnitRule = Field(default_factory=UnitRule)

    @model_validator(mode="after")
    def _page_transport_needs_a_page(self) -> HttpSearch:
        if self.transport == "page" and not self.page_url:
            raise ValueError("transport 'page' requires page_url")
        return self

    @field_validator("headers")
    @classmethod
    def _no_credentials(cls, headers: dict[str, str]) -> dict[str, str]:
        for name, value in headers.items():
            if name.lower() in FORBIDDEN_HEADERS or value.lower().startswith("bearer "):
                raise ValueError(f"header {name!r} looks like a credential; profiles are committed")
        return headers


class SiteProfile(_Model):
    store: str
    version: int = Field(ge=1)
    base_url: str
    # The store's bot protection rejects headless Chromium (HeadlessChrome user agent), so the
    # browser must run with a visible window. Never spoof the identity to get around it (ADR-0007).
    headed: bool = False
    search: HttpSearch | None = None
    login: dict[str, Any] | None = None
    cart: dict[str, Any] | None = None
    history: dict[str, Any] | None = None
    notes: str | None = None


def profile_path(store: str) -> Path:
    return PROFILES_DIR / store / "profile.yaml"


def load_profile(store: str) -> SiteProfile:
    data = yaml.safe_load(profile_path(store).read_text(encoding="utf-8"))
    return SiteProfile.model_validate(data)


def save_profile(profile: SiteProfile) -> Path:
    path = profile_path(profile.store)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
