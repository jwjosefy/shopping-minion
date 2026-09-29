"""Run store: one folder per run under data/runs/ (HLD §4.10). JSON files in v0."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import TypeVar

from pydantic import BaseModel

DEFAULT_RUNS_DIR = Path("data/runs")  # run ids are UTC timestamps
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

M = TypeVar("M", bound=BaseModel)


class RunStore:
    def __init__(self, base: Path = DEFAULT_RUNS_DIR) -> None:
        self.base = base

    def create(self) -> str:
        run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{token_hex(3)}"
        self.path(run_id).mkdir(parents=True)
        return run_id

    def path(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id):
            raise KeyError(run_id)
        return self.base / run_id

    def exists(self, run_id: str) -> bool:
        try:
            return self.path(run_id).is_dir()
        except KeyError:
            return False

    def save_photo(self, run_id: str, image: bytes, media_type: str) -> Path:
        target = self.path(run_id) / f"photo{_EXTENSIONS[media_type]}"
        target.write_bytes(image)
        return target

    def photo(self, run_id: str) -> Path | None:
        return next(iter(sorted(self.path(run_id).glob("photo.*"))), None)

    def save(self, run_id: str, name: str, model: BaseModel) -> None:
        (self.path(run_id) / f"{name}.json").write_text(
            model.model_dump_json(indent=2), encoding="utf-8"
        )

    def load(self, run_id: str, name: str, model_type: type[M]) -> M | None:
        file = self.path(run_id) / f"{name}.json"
        if not file.exists():
            return None
        return model_type.model_validate_json(file.read_text(encoding="utf-8"))
