from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from icoder.agent import Agent
from icoder.llm.base import ChatResponse, LlmClient, Message, StreamListener, ToolCall, ToolDefinition
from icoder.tools import create_default_registry


class FileReadingLlm(LlmClient):
    def __init__(self) -> None:
        self.turn = 0
        self.observation = ""

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
        self.turn += 1
        if self.turn == 1:
            assert tools is not None
            assert any(tool["function"]["name"] == "read_file" for tool in tools)
            return ChatResponse(
                tool_calls=(ToolCall("read-1", "read_file", '{"path":"note.txt"}'),)
            )
        self.observation = str(messages[-1]["content"])
        return ChatResponse(content=f"The file says: {self.observation.strip()}")

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None,
        listener: StreamListener,
    ) -> ChatResponse:
        return self.chat(messages, tools)


def test_agent_and_default_registry_complete_file_read_flow(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello from workspace", encoding="utf-8")
    llm = FileReadingLlm()
    agent = Agent(llm, create_default_registry(tmp_path), workspace=tmp_path)

    answer = agent.run("What does note.txt say?")

    assert answer == "The file says: hello from workspace"
    assert llm.turn == 2
