import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from shopping_minion.intake import LLMIntake, _LLMItem, _LLMList, _to_contract, validate_image


def test_to_contract_normalizes_model_output():
    item = _to_contract(
        _LLMItem(
            name=" Presunto ",
            quantity=0,
            unit="g",
            constraints=[" fatiado ", ""],
            needs_clarification=False,
            source_line="Presunto",
        )
    )
    assert item.name == "Presunto"
    assert item.quantity is None and item.unit is None  # zero means "not written"
    assert item.constraints == ["fatiado"]


def test_validate_image_rejects_unsupported_type():
    with pytest.raises(ValueError, match="unsupported"):
        validate_image(b"x", "image/heic")


def test_validate_image_rejects_oversized():
    with pytest.raises(ValueError, match="5 MB"):
        validate_image(b"x" * (5 * 1024 * 1024 + 1), "image/jpeg")


class _StructuredStub:
    def __init__(self, result):
        self.result = result
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.result


def test_llm_intake_sends_image_and_returns_contract(monkeypatch):
    stub = _StructuredStub(
        _LLMList(
            items=[
                _LLMItem(
                    name="feijão",
                    quantity=None,
                    unit=None,
                    constraints=["normal", "não preto"],
                    needs_clarification=False,
                    source_line="Feijão normal / preto não",
                )
            ]
        )
    )
    model = GenericFakeChatModel(messages=iter([]))
    monkeypatch.setattr(type(model), "with_structured_output", lambda self, *a, **k: stub)

    result = LLMIntake(model).transcribe(b"\xff\xd8fake", "image/jpeg")

    assert [i.name for i in result.items] == ["feijão"]
    image_block = stub.messages[1].content[0]
    assert image_block["type"] == "image" and image_block["mime_type"] == "image/jpeg"
