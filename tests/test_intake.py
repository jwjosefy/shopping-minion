import json
import subprocess
from pathlib import Path
from typing import get_args

import pytest
import yaml

from shopping_minion import cli
from shopping_minion.intake import INTAKE_SCHEMA, IntakeError, build_command, transcribe
from shopping_minion.items import Item, Quantity

ITEM = {
    "source_line": "Presunto 600 g",
    "name": "presunto",
    "search_term": "presunto",
    "constraints": [],
    "brand": None,
    "quantity": {"value": 600, "unit": "g"},
    "needs_review": False,
}


def envelope(**overrides) -> dict:
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps({"items": [ITEM]}),
        "structured_output": {"items": [ITEM]},
    }
    return base | overrides


class FakeRunner:
    """Stands in for subprocess.run: records the call, returns a canned process."""

    def __init__(self, stdout="", stderr="", returncode=0, raises=None):
        self.proc = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.raises = raises
        self.calls: list[tuple[list[str], Path]] = []
        self.files: list[str] = []  # what was in cwd during the call

    def __call__(self, args, cwd):
        self.calls.append((args, cwd))
        self.files = sorted(p.name for p in cwd.iterdir())
        if self.raises:
            raise self.raises
        return self.proc


@pytest.fixture
def photo(tmp_path) -> Path:
    path = tmp_path / "lista.jpg"
    path.write_bytes(b"not really a jpeg")
    return path


def test_schema_and_item_stay_in_sync():
    item_schema = INTAKE_SCHEMA["properties"]["items"]["items"]
    assert set(item_schema["properties"]) == set(Item.model_fields)
    assert set(item_schema["required"]) == set(Item.model_fields)

    quantity_schema = next(
        s for s in item_schema["properties"]["quantity"]["anyOf"] if s["type"] == "object"
    )
    assert set(quantity_schema["properties"]) == set(Quantity.model_fields)
    assert set(quantity_schema["required"]) == set(Quantity.model_fields)
    assert quantity_schema["properties"]["unit"]["enum"] == list(
        get_args(Quantity.model_fields["unit"].annotation)
    )


def test_every_schema_object_forbids_extra_properties():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(INTAKE_SCHEMA)


def test_nullable_fields_accept_null():
    props = INTAKE_SCHEMA["properties"]["items"]["items"]["properties"]
    assert props["brand"]["type"] == ["string", "null"]
    assert {"type": "null"} in props["quantity"]["anyOf"]


def test_transcribe_runs_claude_in_a_temp_dir_with_a_copy(photo):
    runner = FakeRunner(stdout=json.dumps(envelope()))
    items = transcribe(photo, runner=runner)

    assert items == [Item.model_validate(ITEM)]
    args, cwd = runner.calls[0]
    assert args[:2] == ["claude", "-p"]
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--tools") + 1] == "Read"
    assert args[args.index("--output-format") + 1] == "json"
    assert "--no-session-persistence" in args
    assert json.loads(args[args.index("--json-schema") + 1]) == INTAKE_SCHEMA
    assert "handwritten grocery list" in args[args.index("--system-prompt") + 1]
    assert cwd != photo.parent and cwd.name.startswith("shopping-minion-ocr-")
    assert runner.files == [photo.name]  # the request names a real file inside cwd...
    assert str(cwd / photo.name) in args[2]  # ...by absolute path
    assert not cwd.exists()  # cleaned up afterwards
    assert photo.exists()  # the original is untouched


def test_build_command_names_the_copy(tmp_path):
    copy = tmp_path / "x.jpg"
    assert str(copy) in build_command(copy)[2]


def test_transcribe_names_every_photo_in_upload_order(tmp_path):
    pages = []
    for name in ("b.jpg", "a.jpg", "c.jpg"):  # not alphabetical: the order given is what counts
        (tmp_path / name).write_bytes(name.encode())
        pages.append(tmp_path / name)
    runner = FakeRunner(stdout=json.dumps(envelope()))
    assert transcribe(pages, runner=runner) == [Item.model_validate(ITEM)]

    args, cwd = runner.calls[0]
    assert runner.files == ["1-b.jpg", "2-a.jpg", "3-c.jpg"]
    request = args[2]
    assert "3 images" in request and "pages" in request
    at = [request.index(str(cwd / name)) for name in runner.files]
    assert at == sorted(at)  # named in order
    assert not cwd.exists()


def test_same_file_name_from_two_folders_does_not_collide(tmp_path):
    pages = []
    for folder in ("x", "y"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "lista.jpg").write_bytes(folder.encode())
        pages.append(tmp_path / folder / "lista.jpg")
    runner = FakeRunner(stdout=json.dumps(envelope()))
    transcribe(pages, runner=runner)
    assert runner.files == ["1-lista.jpg", "2-lista.jpg"]


def test_a_one_item_list_of_photos_is_the_single_photo_request(photo):
    runner = FakeRunner(stdout=json.dumps(envelope()))
    transcribe([photo], runner=runner)
    assert runner.files == [photo.name]
    assert runner.calls[0][0][2].startswith("Transcribe the grocery list in the file ")


def test_the_prompt_says_the_images_are_pages_of_one_list():
    command = build_command([Path("/t/1-a.jpg"), Path("/t/2-b.jpg")])
    prompt = command[command.index("--system-prompt") + 1]
    assert "pages of one list" in prompt and "page order" in prompt


def test_no_photos_raises():
    with pytest.raises(IntakeError, match="no photos"):
        transcribe([], runner=FakeRunner())


def test_result_string_is_the_fallback(photo):
    stdout = json.dumps({k: v for k, v in envelope().items() if k != "structured_output"})
    assert transcribe(photo, runner=FakeRunner(stdout=stdout))[0].name == "presunto"


@pytest.mark.parametrize(
    ("runner", "needle"),
    [
        (FakeRunner(stderr="Not logged in", returncode=1), "Not logged in"),
        (FakeRunner(stdout="hello"), "not JSON"),
        (FakeRunner(stdout="[1]"), "not an object"),
        (FakeRunner(stdout=json.dumps(envelope(is_error=True, result="boom"))), "boom"),
        (
            FakeRunner(stdout=json.dumps(envelope(subtype="error_max_turns"))),
            "error_max_turns",
        ),
        (
            FakeRunner(stdout=json.dumps({"subtype": "success", "result": "no json here"})),
            "not JSON",
        ),
        (FakeRunner(stdout=json.dumps(envelope(structured_output={"things": []}))), "items"),
        (
            FakeRunner(
                stdout=json.dumps(
                    envelope(
                        structured_output={
                            "items": [{**ITEM, "quantity": {"value": 0, "unit": "g"}}]
                        }
                    )
                )
            ),
            "validate",
        ),
        (
            FakeRunner(raises=subprocess.TimeoutExpired(cmd="claude", timeout=1)),
            "timed out",
        ),
        (FakeRunner(raises=FileNotFoundError("claude")), "could not run claude"),
    ],
)
def test_errors_raise_intake_error(photo, runner, needle):
    with pytest.raises(IntakeError, match=needle):
        transcribe(photo, runner=runner)


def test_missing_second_photo_raises(photo, tmp_path):
    with pytest.raises(IntakeError, match="not found"):
        transcribe([photo, tmp_path / "nope.jpg"], runner=FakeRunner())


def test_missing_photo_raises(tmp_path):
    with pytest.raises(IntakeError, match="not found"):
        transcribe(tmp_path / "nope.jpg", runner=FakeRunner())


def test_cli_ocr_writes_yaml(tmp_path, monkeypatch, capsys):
    items = [Item.model_validate({**ITEM, "name": "feijão", "constraints": ["não preto"]})]
    monkeypatch.setattr("shopping_minion.intake.transcribe", lambda photo: items)
    out = tmp_path / "lista.yaml"

    cli.main(["ocr", str(tmp_path / "p.jpg"), "-o", str(out)])

    text = out.read_text(encoding="utf-8")
    assert "feijão" in text  # allow_unicode
    assert yaml.safe_load(text)["items"][0]["constraints"] == ["não preto"]
    assert "1 items" in capsys.readouterr().err


def test_cli_ocr_takes_several_photos_in_order(tmp_path, monkeypatch, capsys):
    pages = []
    for name in ("p2.jpg", "p1.jpg"):
        (tmp_path / name).write_bytes(b"x")
        pages.append(tmp_path / name)
    runner = FakeRunner(stdout=json.dumps(envelope()))
    monkeypatch.setattr(
        "shopping_minion.intake.transcribe", lambda photos: transcribe(photos, runner=runner)
    )

    cli.main(["ocr", *map(str, pages)])

    assert yaml.safe_load(capsys.readouterr().out)["items"][0]["name"] == "presunto"
    assert runner.files == ["1-p2.jpg", "2-p1.jpg"]


def test_cli_ocr_prints_to_stdout_and_reports_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "shopping_minion.intake.transcribe", lambda photo: [Item.model_validate(ITEM)]
    )
    cli.main(["ocr", "p.jpg"])
    assert yaml.safe_load(capsys.readouterr().out)["items"][0]["name"] == "presunto"

    def fail(photo):
        raise IntakeError("nope")

    monkeypatch.setattr("shopping_minion.intake.transcribe", fail)
    with pytest.raises(SystemExit) as exc:
        cli.main(["ocr", "p.jpg"])
    assert exc.value.code == 1
    assert "nope" in capsys.readouterr().err
