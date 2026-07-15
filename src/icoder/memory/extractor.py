"""LLM-driven extraction of durable, structured memories."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from icoder.llm.base import LlmClient, Message
from icoder.memory.long_term import MemoryEntry, MemoryType, validate_memory_entry

MAX_EXTRACTED_MEMORIES = 3
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_EXTRACTION_PROMPT = """你是 ICoder 的长期记忆提取器。请从输入中提取以后仍有价值的稳定信息。

只允许以下类型：
- user：用户长期偏好或习惯
- project：项目架构、命令、约定或稳定事实
- correction：用户对错误操作或不符合要求行为的纠正
- resource：外部文档、接口或资源信息

禁止保存密码、API Key、Token、Cookie、私钥等秘密；不要保存闲聊、一次性任务或未经确认的推测。
最多提取 3 条。name 使用简短关键字，不包含类型前缀或扩展名。
只输出严格 JSON，不要输出 Markdown 或解释：
{"memories":[{"type":"project","name":"测试命令","description":"项目使用 pytest 运行测试","content":"完整、准确的记忆内容"}]}
没有值得保存的内容时输出：{"memories":[]}"""


class MemoryExtractionError(ValueError):
    """Raised when a model does not return valid structured memories."""


class MemoryExtractor:
    def extract_text(self, content: str, llm_client: LlmClient) -> tuple[MemoryEntry, ...]:
        text = content.strip()
        if not text:
            raise ValueError("memory content cannot be empty")
        return self._extract(f"请从以下用户明确要求保存的内容中提取记忆：\n\n{text}", llm_client)

    def extract_messages(
        self,
        messages: Sequence[Message],
        llm_client: LlmClient,
    ) -> tuple[MemoryEntry, ...]:
        rendered: list[str] = []
        for message in messages:
            role = str(message.get("role", "unknown"))
            content = message.get("content")
            if content:
                rendered.append(f"[{role}] {content}")
            if message.get("tool_calls"):
                rendered.append(f"[{role} tool_calls] {message['tool_calls']}")
        return self._extract(
            "请从以下近期交互中提取长期记忆：\n\n" + "\n".join(rendered),
            llm_client,
        )

    def _extract(self, prompt: str, llm_client: LlmClient) -> tuple[MemoryEntry, ...]:
        response = llm_client.chat(
            [
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
        if response.has_tool_calls or not response.content.strip():
            raise MemoryExtractionError("memory extraction returned no JSON content")
        payload = _parse_json_object(response.content)
        raw_memories = payload.get("memories")
        if not isinstance(raw_memories, list):
            raise MemoryExtractionError("memory extraction JSON must contain a memories array")
        if len(raw_memories) > MAX_EXTRACTED_MEMORIES:
            raise MemoryExtractionError(
                f"memory extraction returned more than {MAX_EXTRACTED_MEMORIES} memories"
            )
        entries: list[MemoryEntry] = []
        for raw in raw_memories:
            if not isinstance(raw, dict):
                raise MemoryExtractionError("each extracted memory must be an object")
            try:
                memory_type = MemoryType(str(raw["type"]).strip().lower())
                entry = MemoryEntry(
                    type=memory_type,
                    name=_required_string(raw, "name"),
                    description=_required_string(raw, "description"),
                    content=_required_string(raw, "content"),
                )
                entries.append(validate_memory_entry(entry))
            except (KeyError, ValueError, TypeError) as exc:
                raise MemoryExtractionError(f"invalid extracted memory: {exc}") from exc
        return tuple(entries)


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = _JSON_FENCE.sub("", content.strip()).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MemoryExtractionError(f"invalid memory extraction JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MemoryExtractionError("memory extraction result must be a JSON object")
    return payload


def _required_string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return result.strip()