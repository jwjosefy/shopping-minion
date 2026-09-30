import asyncio
from decimal import Decimal

import pytest

from shopping_minion.browser import browser_provider
from shopping_minion.catalog.profile import SiteProfile
from shopping_minion.contracts import Candidate, UnitOfSale
from shopping_minion.discovery.tools import (
    DiscoverySession,
    build_tools,
    click_blocked,
    field_blocked,
    format_candidates,
    format_detail,
    format_responses,
    on_store_domain,
    parse_search_section,
    registrable_domain,
    truncate,
)

BASE = "https://store.example.com.br/"

VALID_SECTION = """
steps:
  - open: "{base_url}busca/{query}"
results:
  wait_for: ".product"
  from_dom:
    item: ".product"
    extract:
      name: { selector: ".name" }
      price: { selector: ".price" }
      link: { selector: "a", attr: "href" }
    fields: { id: link, name: name, url: "/p/{link}", price: price }
"""


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://store.example.com.br/busca/atum", True),
        ("http://store.example.com.br/", True),
        ("https://www.store.example.com.br/x", True),
        ("https://STORE.example.com.br/x", True),
        ("https://evilexample.com.br/", False),  # look-alike, not a subdomain
        ("https://example.com.br.evil.com/", False),
        ("https://store.example.com.br.evil.com/", False),
        ("https://other.com.br/", False),
        ("https://maps.googleapis.com/", False),
        ("javascript:alert(1)", False),
        ("file:///etc/passwd", False),
    ],
)
def test_domain_check(url, expected):
    assert on_store_domain(url, "https://www.store.example.com.br/") is expected


def test_registrable_domain():
    assert registrable_domain("https://www.a.example.com.br/x") == "example.com.br"
    assert registrable_domain("https://shop.example.com/") == "example.com"


@pytest.mark.parametrize(
    ("selector", "text"),
    [
        ("button.add-to-cart", ""),
        ("text=Entrar", ""),
        ("#login", ""),
        ("button", "Adicionar ao carrinho"),
        ("a", "Finalizar compra"),
        ("a", "Sign in"),
        ("button", "Pagar"),
        ("a.checkout", ""),
        ("button", "Comprar agora"),
        ("a", "Cadastre-se"),
        ("a", "Esqueci minha senha"),
    ],
)
def test_click_is_refused(selector, text):
    assert click_blocked(selector, text)


@pytest.mark.parametrize(
    ("selector", "text"),
    [
        ("button.close-modal", "Fechar"),
        ("text=Açougue", ""),
        ("a.category", "Bebidas"),
        ("nav a", ""),
    ],
)
def test_click_is_allowed(selector, text):
    assert click_blocked(selector, text) is None


@pytest.mark.parametrize(
    "attrs",
    [
        {"type": "password"},
        {"name": "user_email"},
        {"id": "cpf"},
        {"placeholder": "Digite seu CEP"},
        {"name": "telefone"},
        {"placeholder": "Endereço de entrega"},
        {"id": "phone-number"},
        {"name": "address"},
        {"placeholder": "Senha"},
    ],
)
def test_typing_is_refused(attrs):
    assert field_blocked("input", attrs)


def test_typing_is_refused_by_selector():
    assert field_blocked("input[type=password]")


@pytest.mark.parametrize(
    "attrs",
    [
        {"type": "search", "name": "q", "id": "search", "placeholder": "Buscar produtos"},
        {"type": "text", "name": "receptor", "placeholder": "O que você procura?"},
        {"type": None},
    ],
)
def test_typing_is_allowed(attrs):
    assert field_blocked("input[type=search]", attrs) is None


def test_format_responses_shows_path_not_host():
    records = [
        ("https://api.other-host.example/v1/search?q=atum&page=1", {"hits": [1, 2], "total": 2}),
        ("https://store.example.com.br/x/y", ["a", "b", "c"]),
    ]
    text = format_responses(records)
    assert "/v1/search?q=atum&page=1" in text
    assert "keys: hits, total" in text
    assert "(a list of 3 items)" in text
    assert "other-host" not in text
    assert "https://" not in text


def test_format_responses_numbers_are_stable_under_filter_and_last():
    records = [(f"https://h.example/r{i}", {"i": i}) for i in range(1, 6)]
    text = format_responses(records, contains="r4")
    assert "4. /r4" in text
    assert "5. /r5" in format_responses(records, last=1)


ITEM = {"name": "Tuna can", "pricing": {"price": 4.5, "unit": "UN"}, "tags": ["a", "b"]}
DETAIL_RECORDS = [
    ("https://h.example/search", {"total": 2, "hits": [ITEM, {"name": "Second"}]}),
    ("https://h.example/other", {"x": 1}),
]


def test_format_detail_list_shows_length_and_first_item_in_full():
    text = format_detail(DETAIL_RECORDS, 1, "hits")
    assert "a list of 2 items" in text
    assert '"price": 4.5' in text
    assert "Second" not in text


def test_format_detail_object_lists_keys_first_and_paths_use_brackets():
    text = format_detail(DETAIL_RECORDS, 1)
    assert "an object with 2 keys: total, hits" in text
    nested = format_detail(DETAIL_RECORDS, 1, "hits[0].pricing")
    assert "an object with 2 keys: price, unit" in nested
    assert "4.5" in format_detail(DETAIL_RECORDS, 1, "hits[0].pricing.price")
    assert "/search" in text and "h.example" not in text


def test_format_detail_errors_and_truncation():
    assert "no response number 3" in format_detail(DETAIL_RECORDS, 3)
    assert "no response number 0" in format_detail(DETAIL_RECORDS, 0)
    assert "none recorded" in format_detail([], 1)
    assert "nothing at 'hits[5]'" in format_detail(DETAIL_RECORDS, 1, "hits[5]")
    assert "nothing at 'hits.name'" in format_detail(DETAIL_RECORDS, 1, "hits.name")
    big = [("https://h.example/big", {"items": [{"v": "x" * 20000}]})]
    assert "truncated" in format_detail(big, 1, "items")


def test_format_responses_filter_last_and_truncation():
    records = [(f"https://s.example/r{i}", {"k": "x" * 2000}) for i in range(5)]
    assert format_responses(records, contains="r3").count("keys:") == 1
    assert format_responses(records, last=2).count("keys:") == 2
    assert "truncated" in format_responses(records, last=1)
    assert format_responses([], "") == "no JSON responses."
    assert "matching 'zz'" in format_responses(records, contains="zz")


def _candidate(i, kind="unit", **extra):
    unit = {"unit": {}, "pack": {"pack_size": 6}, "weight_step": {"step_size_g": 500}}[kind]
    return Candidate(
        id=str(i),
        name=f"Atum {i}",
        brand="Marca",
        unit_of_sale=UnitOfSale(kind=kind, **unit),
        price=Decimal("9.9"),
        url=f"https://store.example.com.br/p/{i}",
        **extra,
    )


def test_format_candidates_first_five():
    candidates = [_candidate(1, "pack"), _candidate(2, "weight_step", in_stock=False)]
    candidates += [_candidate(i) for i in range(3, 9)]
    lines = format_candidates(candidates).splitlines()
    assert lines[0] == "8 candidates"
    assert len(lines) == 6
    assert (
        lines[1]
        == "Atum 1 | Marca | 9.90 | pack of 6 | in stock | https://store.example.com.br/p/1"
    )
    assert "weight step 500 g | out of stock" in lines[2]


def test_parse_valid_section():
    profile = parse_search_section(VALID_SECTION, "examplestore", BASE)
    assert isinstance(profile, SiteProfile)
    assert profile.store == "examplestore"
    assert profile.search is not None


def test_parse_invalid_yaml():
    result = parse_search_section("steps: [unclosed", "examplestore", BASE)
    assert result.startswith("error:")
    assert "YAML" in result


def test_parse_not_a_mapping():
    assert parse_search_section("- a\n- b", "examplestore", BASE).startswith("error:")


def test_parse_missing_field():
    assert parse_search_section("steps: []", "examplestore", BASE).startswith("error:")


def test_parse_breaks_profile_rule():
    section = VALID_SECTION.replace("{base_url}busca/{query}", "https://api.elsewhere.com/x")
    result = parse_search_section(section, "examplestore", BASE)
    assert result.startswith("error:")
    assert "base_url" in result


def test_parse_refuses_forbidden_steps():
    click = VALID_SECTION.replace("  - open:", "  - click: 'text=Adicionar ao carrinho'\n  - open:")
    assert parse_search_section(click, "examplestore", BASE).startswith("error:")
    fill = VALID_SECTION.replace(
        "  - open:", "  - fill: 'input[name=email]'\n    value: '{query}'\n  - open:"
    )
    assert parse_search_section(fill, "examplestore", BASE).startswith("error:")


def test_truncate():
    assert truncate("abc", 10) == "abc"
    assert truncate("a" * 50, 10).startswith("a" * 10 + "...")


EXPECTED_TOOLS = {
    "open_page": {"url"},
    "read_page": {"max_chars"},
    "inspect": {"selector"},
    "click": {"selector"},
    "type_text": {"selector", "text", "press_enter"},
    "page_responses": {"contains", "last"},
    "response_detail": {"index", "path"},
    "try_search": {"section_yaml", "query"},
    "submit_search": {"section_yaml"},
}


def test_build_tools_exact_names_and_parameters():
    session = DiscoverySession(None, None, "examplestore", BASE, ["atum"])
    tools = build_tools(session)
    assert {t.name for t in tools} == set(EXPECTED_TOOLS)
    assert len(tools) == len(EXPECTED_TOOLS)
    for t in tools:
        # Only open_page takes a URL, and it is guarded to the store's domain.
        assert set(t.args) == EXPECTED_TOOLS[t.name], t.name


# --- live: the real store, not logged in, at most two pages each -------------------------------


def _live_session(context, page):
    return DiscoverySession(context, page, "andorinha", "https://andorinhaonline.com.br/", ["atum"])


@pytest.mark.live
def test_live_open_page_and_inspect():
    async def go():
        async with browser_provider().session() as context:
            page = await context.new_page()
            tools = {t.name: t for t in build_tools(_live_session(context, page))}
            opened = await tools["open_page"].ainvoke({"url": "https://andorinhaonline.com.br/"})
            found = await tools["inspect"].ainvoke({"selector": "input"})
            return opened, found

    opened, found = asyncio.run(go())
    assert not opened.startswith("error:"), opened
    assert "title:" in opened
    assert "andorinha" in opened.lower()
    assert not found.startswith("error:"), found
    assert not found.startswith("0 elements"), found


@pytest.mark.live
def test_live_page_responses_after_search_page():
    async def go():
        async with browser_provider().session() as context:
            page = await context.new_page()
            tools = {t.name: t for t in build_tools(_live_session(context, page))}
            await tools["open_page"].ainvoke({"url": "https://andorinhaonline.com.br/busca/atum"})
            await page.wait_for_timeout(2000)
            return await tools["page_responses"].ainvoke({})

    listing = asyncio.run(go())
    assert "JSON responses:" in listing, listing
