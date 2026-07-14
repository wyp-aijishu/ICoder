from __future__ import annotations

from collections.abc import Sequence

from icoder.llm.base import ChatResponse, LlmClient, Message, ToolDefinition
from icoder.memory.short_term import SUMMARY_HEADING, ShortTermMemory, estimate_user_tokens


class CompactingLlm(LlmClient):
    def __init__(self, *, max_token: int = 100) -> None:
        self.requests: list[list[dict]] = []
        self._max_token = max_token

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def max_token(self) -> int:
        return self._max_token

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> ChatResponse:
        self.requests.append([dict(message) for message in messages])
        return ChatResponse(content="用户希望继续开发；第一项任务已完成。", total_tokens=12)


def add_turn(memory: ShortTermMemory, number: int) -> None:
    memory.append_user(f"问题{number}")
    memory.append({"role": "assistant", "content": f"回答{number}"})


def test_estimates_chinese_and_other_characters() -> None:
    assert estimate_user_tokens("中文中文中文") == 4
    assert estimate_user_tokens("abcdefgh") == 2
    assert estimate_user_tokens("中文abcd") == 3


def test_compaction_keeps_latest_three_complete_turns_in_system_summary() -> None:
    memory = ShortTermMemory("base system")
    for number in range(1, 5):
        add_turn(memory, number)

    assert memory.compact(CompactingLlm(), force=True)

    messages = memory.messages
    assert SUMMARY_HEADING in messages[0]["content"]
    assert "第一项任务已完成" in messages[0]["content"]
    assert [message["content"] for message in messages if message["role"] == "user"] == [
        "问题2",
        "问题3",
        "问题4",
    ]


def test_compaction_never_splits_active_tool_turn() -> None:
    memory = ShortTermMemory("system")
    for number in range(1, 5):
        add_turn(memory, number)
    memory.append_user("当前任务")
    memory.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    )
    memory.append({"role": "tool", "tool_call_id": "call-1", "content": "result"})

    assert memory.compact(CompactingLlm(), force=True)

    assert memory.messages[-3]["content"] == "当前任务"
    assert memory.messages[-2]["tool_calls"][0]["id"] == "call-1"
    assert memory.messages[-1]["tool_call_id"] == "call-1"


def test_prepare_compacts_at_ninety_percent_of_context() -> None:
    memory = ShortTermMemory("system")
    for number in range(1, 5):
        add_turn(memory, number)
    memory.record_usage(ChatResponse(total_tokens=90))
    llm = CompactingLlm(max_token=100)

    assert memory.prepare_for_llm(llm)
    assert len(llm.requests) == 1


def test_provider_usage_replaces_pending_user_estimate() -> None:
    memory = ShortTermMemory("system")
    memory.append_user("abcdefgh")
    assert memory.used_tokens == 2

    memory.record_usage(ChatResponse(total_tokens=10))

    assert memory.used_tokens == 10


def test_manual_compaction_requires_more_than_three_complete_turns() -> None:
    memory = ShortTermMemory("system")
    for number in range(1, 4):
        add_turn(memory, number)

    assert not memory.compact(CompactingLlm(), force=True)