"""OCR of handwritten list photos through `claude -p` (LLD section 3.1)."""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from shopping_minion.items import Item

PROMPT_PATH = Path(__file__).parent / "prompts" / "intake.md"
# The one real call made while building this took 161 s with the photo of list-001 (4 turns of
# Haiku reading the image), so the ~180 s in the LLD leaves almost no margin; 300 s it is.
TIMEOUT_SECONDS = 300

_UNITS = ["un", "g", "kg", "ml", "l", "pct", "cx", "lata", "dz"]

# Mirrors items.Item. The prompt names `quantity` and `unit` as two fields; here they are one
# nullable object, which is what Item.quantity is. Structured outputs need every property
# required and additionalProperties false on every object.
INTAKE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "source_line",
                    "name",
                    "search_term",
                    "constraints",
                    "brand",
                    "quantity",
                    "needs_review",
                ],
                "properties": {
                    "source_line": {"type": "string"},
                    "name": {"type": "string"},
                    "search_term": {"type": "string"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "brand": {"type": ["string", "null"]},
                    "quantity": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["value", "unit"],
                                "properties": {
                                    "value": {"type": "number"},
                                    "unit": {"type": "string", "enum": _UNITS},
                                },
                            },
                        ]
                    },
                    "needs_review": {"type": "boolean"},
                },
            },
        }
    },
}


class IntakeError(Exception):
    """The OCR call failed or returned something that is not a list of items."""


# A runner takes the argument list and the working directory and returns the finished process.
Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
    )


DEFAULT_MODEL = "sonnet"


def _request(copies: list[Path]) -> str:
    if len(copies) == 1:
        return f"Transcribe the grocery list in the file {copies[0]}"
    names = "\n".join(f"{n}. {copy}" for n, copy in enumerate(copies, start=1))
    return (
        f"Transcribe the grocery list on these {len(copies)} images. They are its pages, "
        f"in this order:\n{names}"
    )


def build_command(photo_copies: Path | list[Path], model: str = DEFAULT_MODEL) -> list[str]:
    copies = [photo_copies] if isinstance(photo_copies, Path) else list(photo_copies)
    return [
        "claude",
        "-p",
        _request(copies),
        "--model",
        model,
        "--system-prompt",
        PROMPT_PATH.read_text(encoding="utf-8"),
        "--tools",
        "Read",
        "--json-schema",
        json.dumps(INTAKE_SCHEMA),
        "--output-format",
        "json",
        "--no-session-persistence",
    ]


def transcribe(
    photos: Path | list[Path], runner: Runner = _run, model: str = DEFAULT_MODEL
) -> list[Item]:
    """Copy the photos (the pages of one list, in order) to a fresh temp dir, run claude there,
    and return the items."""
    paths = [Path(photos)] if isinstance(photos, (str, Path)) else [Path(p) for p in photos]
    if not paths:
        raise IntakeError("no photos")
    for photo in paths:
        if not photo.is_file():
            raise IntakeError(f"photo not found: {photo}")
    with tempfile.TemporaryDirectory(prefix="shopping-minion-ocr-") as tmp:
        workdir = Path(tmp).resolve()
        copies = []
        for n, photo in enumerate(paths, start=1):
            # Several photos can share a file name (different folders): the page number keeps
            # them apart. A single photo keeps its own name.
            copy = workdir / (photo.name if len(paths) == 1 else f"{n}-{photo.name}")
            shutil.copyfile(photo, copy)
            copies.append(copy)
        try:
            proc = runner(build_command(copies, model), workdir)
        except subprocess.TimeoutExpired as exc:
            raise IntakeError(f"claude timed out after {TIMEOUT_SECONDS} s") from exc
        except OSError as exc:
            raise IntakeError(f"could not run claude: {exc}") from exc
    return parse_output(proc)


def parse_output(proc: subprocess.CompletedProcess[str]) -> list[Item]:
    """Turn the finished `claude -p --output-format json` process into items.

    Envelope observed on a real call (claude 2.1.284): one JSON object on stdout with the keys
    type, subtype ("success"), is_error, result, structured_output, num_turns, stop_reason,
    terminal_reason, duration_ms, duration_api_ms, total_cost_usd, usage, modelUsage, session_id,
    uuid, permission_denials, plus a few timing and stats keys.
    The schema-validated answer is `structured_output` (a dict: {"items": [...]}). `result` holds
    the same JSON as a string; it is only a fallback here.
    """
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise IntakeError(f"claude exited with code {proc.returncode}: {detail}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise IntakeError(f"claude output is not JSON ({exc}): {proc.stdout[:200]!r}") from exc
    if not isinstance(envelope, dict):
        raise IntakeError("claude output is JSON but not an object")
    if envelope.get("is_error") or envelope.get("subtype", "success") != "success":
        detail = envelope.get("result") or "no detail"
        raise IntakeError(
            f"claude reported an error (subtype {envelope.get('subtype')}): {detail}. "
            f"stderr: {proc.stderr.strip()}"
        )

    payload = envelope.get("structured_output")
    if payload is None and isinstance(envelope.get("result"), str):
        try:
            payload = json.loads(envelope["result"])
        except json.JSONDecodeError as exc:
            raise IntakeError("envelope has no structured_output and result is not JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise IntakeError('result is not {"items": [...]}')
    try:
        return [Item.model_validate(raw) for raw in payload["items"]]
    except ValidationError as exc:
        raise IntakeError(f"result does not validate as items: {exc}") from exc
