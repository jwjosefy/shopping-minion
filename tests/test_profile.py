import pytest
import yaml
from pydantic import ValidationError

from shopping_minion.catalog.profile import (
    FieldQuantity,
    FillStep,
    OpenStep,
    SiteProfile,
    load_profile,
    profile_path,
    save_profile,
)

PROFILE_YAML = r"""
store: examplestore
version: 1
base_url: https://store.example/
search:
  steps:
    - open: "{base_url}"
    - fill: "input[name=q]"
      value: "{query}"
    - press: Enter
      field: "input[name=q]"
  results:
    wait_for: "text=/Found \\d+ items/"
    from_response:
      url_matches: "/search/results"
      items: hits
      fields:
        id: id
        name: name
        brand: brandName
        price: pricing.price
        url: "{base_url}p/{id}"
        in_stock: { path: quantity.inStock }
      unit_of_sale:
        weight_when: { path: saleUnit, equals: [KG] }
        step_g: { path: quantity.fraction, scale: 1000 }
        pack_size: { path: name, regex: "(?:c/|lv)\\s*(\\d+)" }
    from_dom:
      item: "[data-test=product-card]"
      extract:
        id: { selector: "a.card", attr: data-id }
        name: { selector: ".name" }
        price: { selector: ".price" }
      fields: { id: id, name: name, price: price, url: "{base_url}p/{id}" }
    from_script:
      script: "window.__APP__.store.products"
      fields: { id: id, name: name, url: "/p/{id}" }
login:
  steps:
    - open: "{base_url}login"
    - fill: "input[type=email]"
      env: STORE_EMAIL
    - fill: "input[type=password]"
      env: STORE_PASSWORD
    - click: "button[type=submit]"
  logged_in_when: "text=My account"
cart:
  add:
    - click: "[data-test=add-to-cart]"
    - wait_for: "[data-test=cart-count]"
  quantity:
    kind: stepper
    click: "[data-test=qty-plus]"
  read:
    wait_for: "[data-test=cart-line]"
    from_dom:
      item: "[data-test=cart-line]"
      extract:
        id: { selector: ".line", attr: data-id }
        name: { selector: ".name" }
      fields: { id: id, name: name, url: "{base_url}p/{id}" }
  remove:
    - click: "[data-test=remove-line]"
  checkout_markers:
    - "text=Checkout"
    - "a[href*=checkout]"
notes: Made-up store used only in tests.
"""


def data() -> dict:
    return yaml.safe_load(PROFILE_YAML)


def test_complete_profile_validates():
    profile = SiteProfile.model_validate(data())
    assert profile.store == "examplestore"
    assert isinstance(profile.search.steps[0], OpenStep)
    assert profile.search.results.from_response.items == "hits"
    assert isinstance(profile.login.steps[1], FillStep)
    assert profile.login.steps[2].env == "STORE_PASSWORD"
    assert profile.cart.quantity.kind == "stepper"


def test_save_and_load_round_trip(tmp_path):
    profile = SiteProfile.model_validate(data())
    path = save_profile(profile, tmp_path)
    assert path == tmp_path / "examplestore" / "profile.yaml"
    assert load_profile("examplestore", tmp_path) == profile


def test_field_quantity_and_minimal_profile():
    minimal = {"store": "examplestore", "version": 1, "base_url": "https://store.example/"}
    assert SiteProfile.model_validate(minimal).search is None
    d = data()
    d["cart"]["quantity"] = {"kind": "field", "fill": "input.qty", "then": [{"press": "Enter"}]}
    assert isinstance(SiteProfile.model_validate(d).cart.quantity, FieldQuantity)


@pytest.mark.parametrize("store", ["Example", "ex ample", "ex_ample", "-x", "x-", "../x", ""])
def test_bad_store_names_rejected(store, tmp_path):
    with pytest.raises(ValueError, match="lowercase letters, digits and hyphens"):
        profile_path(store, tmp_path)
    d = data()
    d["store"] = store
    with pytest.raises(ValidationError, match="lowercase letters, digits and hyphens"):
        SiteProfile.model_validate(d)


def test_version_must_be_at_least_one():
    d = data()
    d["version"] = 0
    with pytest.raises(ValidationError):
        SiteProfile.model_validate(d)


def test_load_rejects_store_mismatch(tmp_path):
    save_profile(SiteProfile.model_validate(data()), tmp_path)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "profile.yaml").write_text(
        (tmp_path / "examplestore" / "profile.yaml").read_text()
    )
    with pytest.raises(ValueError, match="not 'other'"):
        load_profile("other", tmp_path)


# Rule 1: open only pages under base_url


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/busca/{query}",
        "http://store.example/",
        "{query}",
        "/busca/{query}",
        "https://store.example@evil.example/",
        "https://store.example.evil.example/",
    ],
)
def test_open_outside_base_url_rejected(url):
    d = data()
    d["search"]["steps"][0] = {"open": url}
    with pytest.raises(ValidationError, match="must be a page under base_url"):
        SiteProfile.model_validate(d)


def test_open_under_base_url_written_out_is_accepted():
    d = data()
    d["search"]["steps"][0] = {"open": "https://store.example/busca/{query}"}
    SiteProfile.model_validate(d)


def test_open_unknown_placeholder_rejected():
    d = data()
    d["search"]["steps"][0] = {"open": "{base_url}p/{id}"}
    with pytest.raises(ValidationError, match="unknown placeholder"):
        SiteProfile.model_validate(d)


def test_field_map_url_outside_store_rejected():
    d = data()
    d["search"]["results"]["from_response"]["fields"]["url"] = "https://cdn.example/p/{id}"
    with pytest.raises(ValidationError, match="must be a page under base_url"):
        SiteProfile.model_validate(d)


# Rule 2: url_matches


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        (r"https://api.example/search", r"scheme or host"),
        (r"/search\?size=24", r"'=' \(a query value"),
        (r"/store/12345/search", r"three or more digits"),
        (r"/search(", r"not a valid regular expression"),
    ],
)
def test_bad_url_matches_rejected(pattern, message):
    d = data()
    d["search"]["results"]["from_response"]["url_matches"] = pattern
    with pytest.raises(ValidationError, match=message):
        SiteProfile.model_validate(d)


def test_bad_url_matches_in_cart_read_rejected():
    d = data()
    d["cart"]["read"]["from_response"] = {
        "url_matches": "/cart/items/1234",
        "items": "lines",
        "fields": {"id": "id", "name": "name", "url": "/p/{id}"},
    }
    with pytest.raises(ValidationError, match="three or more digits"):
        SiteProfile.model_validate(d)


# Rule 3: no credentials


def test_unknown_fields_rejected():
    d = data()
    d["search"]["headers"] = {"Cookie": "a=b"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        SiteProfile.model_validate(d)
    d = data()
    d["login"]["steps"][1]["password"] = "hunter2"
    with pytest.raises(ValidationError):
        SiteProfile.model_validate(d)


def test_fill_with_value_and_env_rejected():
    d = data()
    d["login"]["steps"][1] = {
        "fill": "input[type=email]",
        "value": "me@x.example",
        "env": "STORE_EMAIL",
    }
    with pytest.raises(ValidationError, match="either 'value' or 'env', not both"):
        SiteProfile.model_validate(d)


def test_fill_needs_a_source():
    d = data()
    d["login"]["steps"][1] = {"fill": "input[type=email]"}
    with pytest.raises(ValidationError, match="needs 'value'"):
        SiteProfile.model_validate(d)


def test_env_name_outside_the_allowed_two_rejected():
    d = data()
    d["login"]["steps"][1] = {"fill": "input[type=email]", "env": "HOME"}
    with pytest.raises(ValidationError):
        SiteProfile.model_validate(d)


def test_env_var_name_as_literal_text_rejected():
    d = data()
    d["login"]["steps"][1] = {"fill": "input[type=email]", "value": "STORE_EMAIL"}
    with pytest.raises(ValidationError, match="environment variable name as text"):
        SiteProfile.model_validate(d)
    d = data()
    d["notes"] = "log in with STORE_PASSWORD"
    with pytest.raises(ValidationError, match="environment variable name as text"):
        SiteProfile.model_validate(d)


@pytest.mark.parametrize("field", ["input[type=password]", "#auth-token", "input[name=cookie]"])
def test_literal_value_in_secret_field_rejected(field):
    d = data()
    d["login"]["steps"][2] = {"fill": field, "value": "s3cret"}
    with pytest.raises(ValidationError, match="looks like a secret field"):
        SiteProfile.model_validate(d)


def test_authorization_text_rejected():
    d = data()
    d["notes"] = "Authorization: Bearer abc.def"
    with pytest.raises(ValidationError, match="authorization header or bearer token"):
        SiteProfile.model_validate(d)


# Sources, scripts, checkout


def test_at_least_one_results_source_required():
    d = data()
    d["search"]["results"] = {"wait_for": "text=Found"}
    with pytest.raises(ValidationError, match="at least one source is required"):
        SiteProfile.model_validate(d)
    d = data()
    d["cart"]["read"] = {}
    with pytest.raises(ValidationError, match="at least one source is required"):
        SiteProfile.model_validate(d)


def test_script_may_not_send_requests():
    d = data()
    d["search"]["results"]["from_script"]["script"] = "fetch('/api/items').then(r => r.json())"
    with pytest.raises(ValidationError, match="may not send requests"):
        SiteProfile.model_validate(d)


def test_step_needs_exactly_one_action():
    d = data()
    d["search"]["steps"][0] = {"open": "{base_url}", "click": "a"}
    with pytest.raises(ValidationError, match="exactly one of"):
        SiteProfile.model_validate(d)


def test_click_on_checkout_marker_rejected():
    d = data()
    d["cart"]["add"].append({"click": "text=Checkout"})
    with pytest.raises(ValidationError, match="checkout_markers"):
        SiteProfile.model_validate(d)


def test_checkout_markers_required_with_cart():
    d = data()
    d["cart"]["checkout_markers"] = []
    with pytest.raises(ValidationError):
        SiteProfile.model_validate(d)
