"""The "never" tests (LLD sections 3.2, 3.7 and 7.6): grep src/ for forbidden names."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# Checkout: the button text seen in M0, and the word itself.
CHECKOUT_PATTERNS = [r"Finalizar pedido", r"checkout"]
# On every order page; it writes to the cart (T12). The history sync must never click it.
ADD_ALL_PATTERNS = [r"Adicionar todos os itens ao carrinho"]
# Anything that talks to the store other than a page in the browser.
HAND_MADE_REQUEST_PATTERNS = [
    r"page\.request",
    r"fetch\(",
    r"requests",
    r"httpx",
    r"urllib\.request",
]


CODE_SUFFIXES = {".py", ".md", ".yaml", ".json"}
# The web page's own files. They call our API with fetch(), so only the checkout guard
# applies to them: no page may link to or click the store's checkout.
WEB_SUFFIXES = {".js", ".html", ".css"}


def hits(patterns: list[str], root: Path = SRC, suffixes: set[str] = CODE_SUFFIXES) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(re.search(p, line, re.IGNORECASE) for p in patterns):
                found.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return found


def test_no_checkout_path_in_src():
    assert hits(CHECKOUT_PATTERNS) == []


def test_no_checkout_path_in_the_web_page():
    assert hits(CHECKOUT_PATTERNS, suffixes=WEB_SUFFIXES) == []


def test_no_add_all_to_cart_in_src():
    assert hits(ADD_ALL_PATTERNS) == []


def test_no_add_all_to_cart_in_the_web_page():
    assert hits(ADD_ALL_PATTERNS, suffixes=WEB_SUFFIXES) == []


def test_no_hand_made_requests_in_src():
    assert hits(HAND_MADE_REQUEST_PATTERNS) == []


def test_guards_find_what_they_look_for(tmp_path):
    (tmp_path / "x.py").write_text("page.request.get(u)\nclick('Finalizar Pedido')\nok = 1\n")
    assert len(hits(HAND_MADE_REQUEST_PATTERNS, tmp_path)) == 1
    assert len(hits(CHECKOUT_PATTERNS, tmp_path)) == 1
    (tmp_path / "y.py").write_text("click('Adicionar todos os itens ao carrinho')\n")
    assert len(hits(ADD_ALL_PATTERNS, tmp_path)) == 1
