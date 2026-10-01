import pytest

from shopping_minion.catalog.browser_catalog import BrowserCatalog
from shopping_minion.catalog.profile import SiteProfile

FIELDS = {"id": "id", "name": "name", "url": "/p/{id}"}


def _profile(**extra) -> SiteProfile:
    return SiteProfile.model_validate(
        {"store": "loja", "version": 1, "base_url": "https://loja.example/", **extra}
    )


def test_a_profile_without_search_is_refused_at_construction():
    with pytest.raises(ValueError, match="search"):
        BrowserCatalog(_profile(), context=object())  # type: ignore[arg-type]


def test_a_profile_with_search_is_accepted_without_touching_the_browser():
    profile = _profile(
        search={
            "steps": [{"open": "{base_url}busca/{query}"}],
            "results": {
                "wait_for": "text=itens",
                "from_response": {"url_matches": "/search", "items": "hits", "fields": FIELDS},
            },
        }
    )
    BrowserCatalog(profile, context=object())  # type: ignore[arg-type]
