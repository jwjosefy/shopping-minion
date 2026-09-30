import pytest

from shopping_minion.catalog.page import (
    ForbiddenStepError,
    ResponseLog,
    ScriptOutcome,
    check_step_allowed,
    render,
    select_candidates,
)
from shopping_minion.catalog.profile import CartRead, ClickStep, FillStep, OpenStep, SearchResults
from shopping_minion.workflow import SiteChangedError

BASE = "https://loja.example/"
FIELDS = {"id": "id", "name": "name", "url": "/p/{id}"}
VALUES = {"base_url": BASE, "query": "leite integral & café", "name": "Leite", "quantity": "2"}


# --- render -----------------------------------------------------------------------------------


def test_render_quotes_values_but_not_base_url_in_urls():
    url = render("{base_url}busca/{query}", VALUES, quote=True)
    assert url == "https://loja.example/busca/leite%20integral%20%26%20caf%C3%A9"


def test_render_leaves_values_as_they_are_for_typed_text():
    assert render("{query} x{quantity}", VALUES, quote=False) == "leite integral & café x2"


def test_render_rejects_unknown_and_missing_placeholders():
    with pytest.raises(ValueError, match="unknown placeholder"):
        render("{base_url}{token}", VALUES, quote=True)
    with pytest.raises(ValueError, match="no value"):
        render("{name}", {"query": "x"}, quote=False)


# --- ResponseLog ------------------------------------------------------------------------------


def test_find_returns_the_most_recent_match_on_path_and_query():
    log = ResponseLog(
        [
            ("https://api.example/search?page=1", {"n": 1}),
            ("https://api.example/other", {"n": 2}),
            ("https://api.example/search?page=2", {"n": 3}),
        ]
    )
    assert log.find(r"/search") == {"n": 3}
    assert log.find(r"page=1") == {"n": 1}
    assert log.find(r"/nothing") is None


def test_find_does_not_match_on_host_and_clear_forgets():
    log = ResponseLog([("https://searchhost.example/x", {"n": 1})])
    assert log.find("searchhost") is None
    log.add("https://a.example/search", {"n": 2})
    log.clear()
    assert log.find("/search") is None


def test_records_returns_a_copy_of_what_was_recorded():
    log = ResponseLog([("https://a.example/one", {"n": 1})])
    log.add("https://a.example/two", {"n": 2})
    records = log.records()
    assert records == [("https://a.example/one", {"n": 1}), ("https://a.example/two", {"n": 2})]
    records.clear()
    assert len(log.records()) == 2


# --- forbidden steps --------------------------------------------------------------------------


def test_a_click_on_a_never_selector_is_forbidden():
    with pytest.raises(ForbiddenStepError):
        check_step_allowed(ClickStep(click="text=Finalizar"), VALUES, ["text=Finalizar"])
    check_step_allowed(ClickStep(click="text=Adicionar"), VALUES, ["text=Finalizar"])


def test_an_open_whose_url_contains_a_never_marker_is_forbidden():
    step = OpenStep(open="{base_url}checkout/{query}")
    with pytest.raises(ForbiddenStepError):
        check_step_allowed(step, VALUES, ["/checkout"])
    check_step_allowed(step, VALUES, [])


def test_fill_steps_are_not_checked_against_never():
    check_step_allowed(FillStep(fill="input", value="{query}"), VALUES, ["input"])


# --- source selection -------------------------------------------------------------------------

RESPONSE = {"url_matches": "/search", "items": "hits", "fields": FIELDS}
DOM = {
    "item": "li",
    "extract": {"id": {"selector": "a", "attr": "data-id"}, "name": {"selector": "a"}},
    "fields": FIELDS,
}
SCRIPT = {"script": "window.state.items", "fields": FIELDS}
BODY = {"hits": [{"id": "r1", "name": "From response"}]}
HTML = '<ul><li><a data-id="d1">From dom</a></li></ul>'


def _sources(*names: str, cls=SearchResults):
    config = {"response": RESPONSE, "dom": DOM, "script": SCRIPT}
    kwargs = {f"from_{n}": config[n] for n in names}
    return cls.model_validate({"wait_for": "x", **kwargs})


def _pick(sources, **inputs):
    inputs = {"response_body": None, "html": None, "script": None} | inputs
    return select_candidates(sources, base_url=BASE, **inputs)


def test_response_wins_over_dom_and_script():
    got = _pick(
        _sources("response", "dom", "script"),
        response_body=BODY,
        html=HTML,
        script=ScriptOutcome(items=[{"id": "s1", "name": "S"}]),
    )
    assert [c.id for c in got] == ["r1"]


def test_dom_is_used_when_no_response_matched():
    got = _pick(_sources("response", "dom"), html=HTML)
    assert [c.id for c in got] == ["d1"]


def test_script_is_used_last_and_mapped_with_its_fields():
    got = _pick(
        _sources("response", "dom", "script"),
        html="<p>nothing here</p>",
        script=ScriptOutcome(items=[{"id": 7, "name": "S"}]),
    )
    assert [c.id for c in got] == ["7"]


def test_empty_list_is_accepted_only_after_the_other_sources_found_nothing():
    got = _pick(_sources("response", "dom"), response_body={"hits": []}, html=HTML)
    assert [c.id for c in got] == ["d1"]


def test_missing_response_and_zero_dom_cards_means_no_results():
    assert _pick(_sources("response", "dom"), html="<p>Nenhum resultado</p>") == []


def test_a_failing_source_is_skipped_when_another_worked():
    got = _pick(_sources("response", "dom"), response_body={"unexpected": 1}, html=HTML)
    assert [c.id for c in got] == ["d1"]


def test_when_every_source_fails_the_error_says_why_for_each():
    with pytest.raises(SiteChangedError) as caught:
        _pick(
            _sources("response", "dom", "script"),
            response_body={"unexpected": 1},
            html=None,
            script=ScriptOutcome(error="the script failed"),
        )
    message = str(caught.value)
    assert "from_response" in message and "from_dom" in message and "from_script" in message
    assert "the script failed" in message


def test_a_script_that_returned_no_list_counts_as_failed():
    with pytest.raises(SiteChangedError, match="from_script"):
        _pick(_sources("script"), script=None)


def test_cart_read_sources_use_the_same_selection():
    got = _pick(_sources("response", cls=CartRead), response_body=BODY)
    assert [c.id for c in got] == ["r1"]


# --- run_steps with a root ---------------------------------------------------------------------


def test_run_steps_refuses_open_inside_a_root_before_doing_anything():
    import asyncio
    from unittest.mock import MagicMock

    from shopping_minion.catalog.page import run_steps

    page, root = MagicMock(), MagicMock()
    steps = [ClickStep(click=".add"), OpenStep(open="{base_url}cart")]
    with pytest.raises(ValueError, match="inside a card"):
        asyncio.run(run_steps(page, steps, VALUES, root=root))
    root.locator.assert_not_called()
    page.goto.assert_not_called()


def test_select_lines_keeps_quantity_and_falls_back_like_select_candidates():
    from shopping_minion.catalog.page import select_lines

    sources = CartRead.model_validate(
        {
            "quantity": "qty",
            "from_response": {"url_matches": "/cart", "items": "lines", "fields": FIELDS},
        }
    )
    lines = select_lines(
        sources,
        base_url=BASE,
        response_body={"lines": [{"id": "1", "name": "Leite", "qty": "2"}]},
        html=None,
        script=None,
    )
    assert [(line.candidate.id, line.quantity) for line in lines] == [("1", 2.0)]
    with pytest.raises(SiteChangedError, match="no way of reading"):
        select_lines(sources, base_url=BASE, response_body=None, html=None, script=None)
