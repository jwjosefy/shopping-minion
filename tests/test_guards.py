"""The "never" tests (LLD sections 3.2, 3.7 and 7.6): grep src/ for forbidden names."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# Checkout: the button text seen in M0, and the word itself.
CHECKOUT_PATTERNS = [r"Finalizar pedido", r"checkout"]
# Anything that talks to the store other than a page in the browser.
HAND_MADE_REQUEST_PATTERNS = [
    r"page\.request",
    r"fetch\(",
    r"requests",
    r"httpx",
    r"urllib\.request",
]


def hits(patterns: list[str], root: Path = SRC) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".json"}:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(re.search(p, line, re.IGNORECASE) for p in patterns):
                found.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return found


def test_no_checkout_path_in_src():
    assert hits(CHECKOUT_PATTERNS) == []


def test_no_hand_made_requests_in_src():
    assert hits(HAND_MADE_REQUEST_PATTERNS) == []


def test_guards_find_what_they_look_for(tmp_path):
    (tmp_path / "x.py").write_text("page.request.get(u)\nclick('Finalizar Pedido')\nok = 1\n")
    assert len(hits(HAND_MADE_REQUEST_PATTERNS, tmp_path)) == 1
    assert len(hits(CHECKOUT_PATTERNS, tmp_path)) == 1
