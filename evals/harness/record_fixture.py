"""Record a store's public search response as an offline fixture (ADR-0004, ADR-0006).

    uv run python evals/harness/record_fixture.py andorinha "feijão" "presunto"

Only public catalog data is recorded, through the store's own profile and browser settings.
Check the file for personal data before committing it.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
from pathlib import Path

from shopping_minion.browser import browser_provider
from shopping_minion.catalog.adapter import fetch
from shopping_minion.catalog.profile import PROFILES_DIR, load_profile


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


async def main(store: str, queries: list[str]) -> None:
    profile = load_profile(store)
    assert profile.search is not None
    async with browser_provider(headless=not profile.headed).session() as context:
        for query in queries:
            payload = await fetch(context, profile.search, query)
            path = Path(PROFILES_DIR / store / "fixtures" / f"search-{slug(query)}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"query": query, "payload": payload}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print(f"{query!r}: {len(payload['hits'])} hits -> {path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
