from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from icoder.llm.base import ChatResponse, LlmClient, Message, ToolDefinition
from icoder.memory.manager import MemoryManager


class MemoryLlm(LlmClient):
    def __init__(self, answer: str, completed: threading.Event | None = None) -> None:
        self.answer = answer
        self.completed = completed

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
        if self.completed:
            self.completed.set()
        return ChatResponse(content=self.answer)


MEMORY_JSON = (
    '{"memories":[{"type":"project","name":"构建命令",'
    '"description":"项目使用 build 命令","content":"运行 build。"}]}'
)


def test_explicit_save_refreshes_system_memory_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager("system", workspace, memory_root=tmp_path / "memory")

    entries = manager.save_explicit("记住构建命令", MemoryLlm(MEMORY_JSON))

    assert entries[0].filename == "[project]构建命令.md"
    assert "[project]构建命令.md: 项目使用 build 命令" in manager.messages[0]["content"]


def test_four_events_trigger_non_blocking_implicit_extraction(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    completed = threading.Event()
    manager = MemoryManager(
        "system",
        workspace,
        memory_root=tmp_path / "memory",
        memory_client_factory=lambda provider, model: MemoryLlm(MEMORY_JSON, completed),
    )
    manager.append_user("one")
    manager.append({"role": "assistant", "content": "answer one"})
    manager.append_user("two")
    manager.append({"role": "assistant", "content": "answer two"})

    assert manager.schedule_implicit_extraction(MemoryLlm("unused"))
    assert completed.wait(2)

    target = manager.long_term.project_dir / "[project]构建命令.md"
    for _ in range(1000):
        if target.exists():
            break
    assert target.exists()