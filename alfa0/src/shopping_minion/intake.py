"""Intake: photo of a handwritten list -> TranscribedList (HLD §4.2).

The model answers in a plain schema (no numeric constraints, which structured outputs don't
support); the result is then validated into the stricter `TranscribedList` contract.
"""

from __future__ import annotations

import base64
import logging
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from shopping_minion.config import ChatRole
from shopping_minion.contracts import TranscribedItem, TranscribedList
from shopping_minion.models import chat_model

log = logging.getLogger(__name__)

SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # API limit per image

SYSTEM_PROMPT = """\
You transcribe photos of handwritten grocery lists from Brazilian households into structured \
items. The lists are in Portuguese, often in cursive, written by different people.

For each line on the paper, read what is written and turn it into one or more items:

- A slash usually separates items: "Açúcar / refri / água gás" is three items. But a slash can \
also introduce a constraint on the same item: "Feijão normal / preto não" is one item (feijão) \
with constraints ["normal", "não preto"]. Decide from meaning, not from punctuation.
- Qualifiers that narrow the product ("normal", "integral", "sem lactose") go in `constraints`.
- Quantities written on the line go in `quantity` and `unit` ("Presunto 600 g" -> quantity 600, \
unit "g"). Leave both empty when nothing is written; never guess a quantity.
- Fix obvious misspellings and expand common abbreviations in `name` ("espaguet" -> \
"espaguete", "refri" -> "refrigerante"), keeping names in Portuguese and lowercase. Brand names \
go in `constraints` with the correct spelling ("melita" -> "Melitta").
- Ignore words that are crossed out.
- A line that names a category rather than a product ("Lanches das crianças") is still an item, \
with `needs_clarification` set to true.
- If the same item appears twice, keep both occurrences; the human reviewer will merge them.
- If you can't read a line with reasonable confidence, transcribe your best reading and set \
`needs_clarification` to true.

Put the line exactly as written (your best reading, before any fixes) in `source_line`. Return \
items in the order they appear on the paper.
"""


class _LLMItem(BaseModel):
    name: str = Field(description="Item name in Portuguese, lowercase, misspellings fixed")
    quantity: float | None = Field(description="Quantity written on the line, if any")
    unit: str | None = Field(description="Unit for the quantity (g, kg, un, pct...), if any")
    constraints: list[str] = Field(description="Qualifiers, negations and brands")
    needs_clarification: bool
    source_line: str = Field(description="The line as written on the paper")


class _LLMList(BaseModel):
    items: list[_LLMItem]


class Intake(Protocol):
    def transcribe(self, image: bytes, media_type: str) -> TranscribedList: ...


class LLMIntake:
    """Transcribes with the primary model; on any error, tries the fallback once (ADR-0011)."""

    def __init__(
        self,
        model: BaseChatModel,
        fallback: BaseChatModel | None = None,
        names: tuple[str, str | None] = ("primary", "fallback"),
    ) -> None:
        self._models = [(names[0], model.with_structured_output(_LLMList, method="json_schema"))]
        if fallback is not None:
            structured = fallback.with_structured_output(_LLMList, method="json_schema")
            self._models.append((names[1] or "fallback", structured))
        self.answered_by: str | None = None

    def transcribe(self, image: bytes, media_type: str) -> TranscribedList:
        validate_image(image, media_type)
        messages = [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                content=[
                    {
                        "type": "image",
                        "base64": base64.b64encode(image).decode("ascii"),
                        "mime_type": media_type,
                    },
                    {"type": "text", "text": "Transcribe this grocery list."},
                ]
            ),
        ]
        for index, (name, structured) in enumerate(self._models):
            try:
                result = structured.invoke(messages)
            except Exception as e:
                if index == len(self._models) - 1:
                    raise
                log.warning(
                    "intake: %s failed (%s: %s); trying fallback", name, type(e).__name__, e
                )
                continue
            assert isinstance(result, _LLMList)
            self.answered_by = name
            return TranscribedList(items=[_to_contract(item) for item in result.items])
        raise AssertionError("unreachable")


def validate_image(image: bytes, media_type: str) -> None:
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise ValueError(f"unsupported image type {media_type!r}; use JPEG, PNG, WebP or GIF")
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError(f"image is {len(image) // 1024} KB; the limit is 5 MB")


def _to_contract(item: _LLMItem) -> TranscribedItem:
    quantity = item.quantity if item.quantity and item.quantity > 0 else None
    return TranscribedItem(
        name=item.name.strip(),
        quantity=quantity,
        unit=(item.unit or None) if quantity else None,
        constraints=[c.strip() for c in item.constraints if c.strip()],
        needs_clarification=item.needs_clarification,
        source_line=item.source_line.strip() or None,
    )


def build_intake(role: ChatRole) -> LLMIntake:
    fallback = role.fallback
    return LLMIntake(
        chat_model(role),
        chat_model(fallback) if fallback else None,
        names=(
            f"{role.provider}:{role.model}",
            f"{fallback.provider}:{fallback.model}" if fallback else None,
        ),
    )
