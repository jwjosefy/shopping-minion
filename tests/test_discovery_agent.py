import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
import yaml
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from shopping_minion.catalog.profile import Search, load_profile
from shopping_minion.contracts import Candidate, UnitOfSale
from shopping_minion.discovery.agent import (
    DOM_EXAMPLE,
    SEARCH_EXAMPLE,
    SYSTEM_PROMPT,
    discover_search,
    query_slug,
)
from shopping_minion.discovery.cli import DEFAULT_QUERIES, add_parser
from shopping_minion.discovery.tools import DiscoverySession, build_tools

BASE = "https://store.example/"
QUERIES = ["atum", "papel higiênico"]


class ScriptedModel(GenericFakeChatModel):
    """Returns a fixed sequence of messages; bind_tools is a no-op."""

    def bind_tools(self, tools, **kwargs):
        return self


def scripted(*messages: AIMessage) -> ScriptedModel:
    return ScriptedModel(messages=iter(messages))


def call(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class FakeContext:
    async def new_page(self):
        return None


class FakeBrowser:
    @asynccontextmanager
    async def session(self, store=None):
        yield FakeContext()


def candidate(name: str) -> Candidate:
    return Candidate(
        id=name,
        name=name,
        brand="Marca",
        unit_of_sale=UnitOfSale(kind="unit"),
        price=Decimal("9.90"),
        url=f"{BASE}p/{name}",
    )


def stub_tools(session):
    @tool
    async def open_page(url: str) -> str:
        """Open a page."""
        return f"title: home\nurl: {url}"

    @tool
    async def try_search(section_yaml: str, query: str) -> str:
        """Try a draft."""
        return f"1 candidates for {query}"

    @tool
    async def submit_search(section_yaml: str) -> str:
        """Submit a draft."""
        session.accepted = Search.model_validate(yaml.safe_load(section_yaml))
        session.candidates = {q: [candidate(f"produto {q}")] for q in session.test_queries}
        return "accepted"

    return [open_page, try_search, submit_search]


def run(model, tmp_path, **kwargs):
    lines: list[str] = []
    result = asyncio.run(
        discover_search(
            "examplestore",
            BASE,
            QUERIES,
            model=model,
            browser=FakeBrowser(),
            log=lines.append,
            tools_factory=stub_tools,
            profiles_dir=tmp_path / "profiles",
            log_dir=tmp_path / "logs",
            **kwargs,
        )
    )
    return result, lines


def test_an_accepted_section_writes_the_profile_and_fixtures(tmp_path):
    model = scripted(
        call("open_page", "1", url=BASE),
        call("try_search", "2", section_yaml=SEARCH_EXAMPLE, query="atum"),
        call("submit_search", "3", section_yaml=SEARCH_EXAMPLE),
        AIMessage(content="Source: response. Units: name regex."),
    )
    result, lines = run(model, tmp_path)

    assert result.accepted is not None
    assert result.reason is None
    assert result.steps == 4
    assert result.final_message == "Source: response. Units: name regex."

    profile = load_profile("examplestore", tmp_path / "profiles")
    assert profile.version == 1
    assert profile.base_url == BASE
    assert profile.search == result.accepted
    assert profile.notes == "Source: response. Units: name regex."
    fixtures = tmp_path / "profiles" / "examplestore" / "fixtures"
    assert sorted(p.name for p in fixtures.iterdir()) == [
        "candidates-atum.json",
        "candidates-papel-higienico.json",
    ]
    data = json.loads((fixtures / "candidates-atum.json").read_text(encoding="utf-8"))
    assert [Candidate.model_validate(item) for item in data] == [candidate("produto atum")]

    text = "\n".join(lines)
    for name in ("open_page", "try_search", "submit_search"):
        assert f"call {name}" in text
    assert "result submit_search: accepted" in text
    assert "result open_page: title: home" in text
    assert result.log_path.parent == tmp_path / "logs"
    assert result.log_path.name.startswith("examplestore-")
    assert result.log_path.read_text(encoding="utf-8") == "\n".join(lines) + "\n"


def test_no_submission_writes_nothing_and_says_why(tmp_path):
    model = scripted(
        call("open_page", "1", url=BASE),
        AIMessage(content="I could not find the search."),
    )
    result, lines = run(model, tmp_path)

    assert result.accepted is None
    assert result.steps == 2
    assert "without an accepted section" in result.reason
    assert result.final_message == "I could not find the search."
    assert not (tmp_path / "profiles").exists()
    assert "call open_page" in "\n".join(lines)


def test_the_step_limit_stops_the_run(tmp_path):
    model = scripted(*[call("open_page", str(i), url=BASE) for i in range(10)])
    result, _ = run(model, tmp_path, max_steps=3)

    assert result.accepted is None
    assert result.steps == 3
    assert "step limit" in result.reason
    assert not (tmp_path / "profiles").exists()


def test_a_model_error_writes_nothing_and_says_why(tmp_path):
    result, _ = run(scripted(call("open_page", "1", url=BASE)), tmp_path)  # then runs out

    assert result.accepted is None
    assert "the run failed" in result.reason
    assert not (tmp_path / "profiles").exists()


def test_an_invalid_store_name_is_refused_before_anything_runs():
    with pytest.raises(ValueError, match="store name"):
        asyncio.run(
            discover_search(
                "Bad Store", BASE, QUERIES, model=scripted(), browser=FakeBrowser(), log=print
            )
        )


# --- the prompt -------------------------------------------------------------------------------


@pytest.mark.parametrize("example", [SEARCH_EXAMPLE, DOM_EXAMPLE])
def test_the_prompt_examples_validate_as_a_search_section(example):
    search = Search.model_validate(yaml.safe_load(example))
    assert search.results.wait_for


def test_the_prompt_embeds_the_examples_and_the_rules():
    assert SEARCH_EXAMPLE in SYSTEM_PROMPT
    assert DOM_EXAMPLE in SYSTEM_PROMPT
    assert "{base_url}busca/{query}" in SEARCH_EXAMPLE
    for phrase in ("from_response", "url_matches", "only through its site", "weight step", "pack"):
        assert phrase in SYSTEM_PROMPT


def test_the_prompt_names_the_exploration_tools():
    tools = build_tools(DiscoverySession(None, None, "examplestore", BASE, ["atum"]))
    for t in tools:
        assert t.name in SYSTEM_PROMPT


# --- helpers and the command ------------------------------------------------------------------


def test_query_slug():
    assert query_slug("papel higiênico") == "papel-higienico"
    assert query_slug("filé de peito de frango") == "file-de-peito-de-frango"
    assert query_slug("???") == "query"


def parse(argv):
    parser = argparse.ArgumentParser()
    add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def test_discover_parser_defaults():
    args = parse(["discover", "examplestore", "--url", BASE])
    assert (args.command, args.store, args.url) == ("discover", "examplestore", BASE)
    assert args.query is None
    assert args.max_steps == 120
    assert DEFAULT_QUERIES == ["atum", "papel higiênico", "filé de peito de frango"]


def test_discover_parser_arguments():
    args = parse(
        ["discover", "s", "--url", BASE, "--query", "leite", "--query", "ovos", "--max-steps", "5"]
    )
    assert args.query == ["leite", "ovos"]
    assert args.max_steps == 5


def test_discover_requires_a_url():
    with pytest.raises(SystemExit):
        parse(["discover", "examplestore"])
