from __future__ import annotations

from collections.abc import Sequence

import pytest

from icoder.llm.base import ChatResponse, LlmClient, Message, ToolDefinition
from icoder.memory.extractor import MemoryExtractionError, MemoryExtractor
from icoder.memory.long_term import MemoryType


class ExtractionLlm(LlmClient):
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[list[dict]] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> ChatResponse:
        self.requests.append([dict(message) for message in messages])
        return ChatResponse(content=self.answer)


def test_extracts_valid_structured_memories() -> None:
    llm = ExtractionLlm(
        '{"memories":[{"type":"user","name":"回复偏好",'
        '"description":"用户偏好简洁中文","content":"回答应使用简洁中文。"}]}'
    )

    entries = MemoryExtractor().extract_text("记住用简洁中文回答", llm)

    assert entries[0].type is MemoryType.USER
    assert entries[0].filename == "[user]回复偏好.md"


def test_accepts_no_valuable_memory() -> None:
    assert MemoryExtractor().extract_text("你好", ExtractionLlm('{"memories":[]}')) == ()


def test_rejects_invalid_extraction_json() -> None:
    with pytest.raises(MemoryExtractionError, match="invalid memory extraction JSON"):
        MemoryExtractor().extract_text("save", ExtractionLlm("not-json"))