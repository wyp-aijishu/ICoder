"""Short-term protocol history, token accounting, and conversation compaction."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from icoder.llm.base import ChatResponse, LlmClient, ToolDefinition

DEFAULT_COMPACTION_THRESHOLD = 0.9
DEFAULT_PRESERVED_TURNS = 3
SUMMARY_HEADING = "## 动态对话摘要"

_COMPACTION_SYSTEM_PROMPT = """你是 ICoder 的对话压缩器。请将提供的较早对话压缩为简洁、准确的中文摘要。
必须保留：用户意图与约束、已完成任务、关键决定、修改过的文件、未完成任务、错误及用户纠正。
不要补充对话中不存在的信息。只输出 Markdown 摘要正文，不要输出解释或代码围栏。"""


class ShortTermMemory:
    """Own the mutable chat history and compact old completed turns."""

    def __init__(
        self,
        system_prompt: str,
        *,
        compaction_threshold: float = DEFAULT_COMPACTION_THRESHOLD,
        preserved_turns: int = DEFAULT_PRESERVED_TURNS,
    ) -> None:
        if not 0 < compaction_threshold <= 1:
            raise ValueError("compaction_threshold must be between zero and one")
        if preserved_turns < 1:
            raise ValueError("preserved_turns must be greater than zero")
        self._base_system_prompt = system_prompt
        self._summary = ""
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._used_tokens = 0
        self._pending_user_tokens = 0
        self._compaction_threshold = compaction_threshold
        self._preserved_turns = preserved_turns

    @property
    def messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(_copy_message(message) for message in self._messages)

    @property
    def used_tokens(self) -> int:
        return self._used_tokens

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def completed_turn_count(self) -> int:
        return len(self._completed_turns())

    def clear(self) -> None:
        self._summary = ""
        self._used_tokens = 0
        self._pending_user_tokens = 0
        self._messages = [{"role": "system", "content": self._base_system_prompt}]

    def set_base_system_prompt(self, system_prompt: str) -> None:
        """Replace dynamic system context while preserving summary and turns."""
        self._base_system_prompt = system_prompt
        self._messages[0] = {
            "role": "system",
            "content": self._system_prompt_with_summary() if self._summary else system_prompt,
        }

    def append_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        estimated = estimate_user_tokens(content)
        self._used_tokens += estimated
        self._pending_user_tokens += estimated

    def append(self, message: dict[str, Any]) -> None:
        self._messages.append(message)

    def record_usage(self, response: ChatResponse) -> None:
        """Accumulate provider-reported interaction tokens."""
        if response.total_tokens > 0:
            self._used_tokens -= self._pending_user_tokens
            self._used_tokens += response.total_tokens
            self._pending_user_tokens = 0

    def prepare_for_llm(
        self,
        llm_client: LlmClient,
        tools: Sequence[ToolDefinition] | None = None,
    ) -> bool:
        """Compact old turns before an LLM call when 90% of context is reached."""
        del tools  # Reserved for future protocol-overhead accounting.
        threshold = math.floor(llm_client.max_token * self._compaction_threshold)
        if self._used_tokens < threshold:
            return False
        return self.compact(llm_client)

    def compact(self, llm_client: LlmClient, *, force: bool = False) -> bool:
        """Summarize old completed turns while retaining the latest three."""
        completed = self._completed_turns()
        if len(completed) <= self._preserved_turns:
            return False

        compacted_turns = completed[: -self._preserved_turns]
        first_index = compacted_turns[0][0]
        end_index = compacted_turns[-1][1]
        source_messages = self._messages[first_index:end_index]
        prompt = _build_compaction_input(self._summary, source_messages)
        response = llm_client.chat(
            [
                {"role": "system", "content": _COMPACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
        if response.has_tool_calls or not response.content.strip():
            raise ValueError("conversation compaction returned no usable summary")

        self._summary = response.content.strip()
        retained = self._messages[end_index:]
        self._messages = [
            {"role": "system", "content": self._system_prompt_with_summary()},
            *retained,
        ]
        summary_tokens = response.completion_tokens or estimate_user_tokens(self._summary)
        self._used_tokens = summary_tokens + _estimate_retained_tokens(retained)
        # # --- 调试输出 ---
        # print("\n" + "=" * 60)
        # print("📋 压缩摘要:")
        # print("-" * 40)
        # print(self._summary)
        # print("-" * 40)
        # print("📨 压缩后的历史对话 (messages):")
        # for i, msg in enumerate(self._messages):
        #     role = msg.get("role", "?")
        #     content = msg.get("content", "")
        #     if isinstance(content, str) and len(content) > 200:
        #         content = content[:200] + "..."
        #     print(f"  [{i}] {role}: {content}")
        # print("=" * 60 + "\n")
        # # --- 调试输出结束 ---
        return True

    def _completed_turns(self) -> list[tuple[int, int]]:
        starts = [
            index
            for index, message in enumerate(self._messages)
            if message.get("role") == "user"
        ]
        completed: list[tuple[int, int]] = []
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(self._messages)
            turn = self._messages[start:end]
            if _is_completed_turn(turn):
                completed.append((start, end))
        return completed

    def _system_prompt_with_summary(self) -> str:
        return f"{self._base_system_prompt.rstrip()}\n\n{SUMMARY_HEADING}\n\n{self._summary}\n"


def estimate_user_tokens(content: str) -> int:
    """Estimate Chinese at 1.5 characters/token and other text at 4 chars/token."""
    chinese_count = sum(1 for char in content if _is_chinese(char))
    other_count = len(content) - chinese_count
    return max(1, math.ceil(chinese_count / 1.5 + other_count / 4))


def _is_chinese(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _is_completed_turn(turn: Sequence[dict[str, Any]]) -> bool:
    if not turn or turn[0].get("role") != "user":
        return False
    last = turn[-1]
    return last.get("role") == "assistant" and not last.get("tool_calls")


def _build_compaction_input(
    previous_summary: str,
    messages: Sequence[dict[str, Any]],
) -> str:
    sections: list[str] = []
    if previous_summary:
        sections.append(f"已有摘要：\n{previous_summary}")
    rendered = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = message.get("content")
        if content:
            rendered.append(f"[{role}] {content}")
        if message.get("tool_calls"):
            rendered.append(f"[{role} tool_calls] {message['tool_calls']}")
    sections.append("需要压缩的较早对话：\n" + "\n".join(rendered))
    return "\n\n".join(sections)


def _estimate_retained_tokens(messages: Sequence[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and content:
            total += estimate_user_tokens(content)
    return total


def _copy_message(message: dict[str, Any]) -> dict[str, Any]:
    copied = dict(message)
    if "tool_calls" in copied:
        copied["tool_calls"] = [
            {
                **dict(call),
                "function": dict(call.get("function", {})),
            }
            for call in copied["tool_calls"]
        ]
    return copied