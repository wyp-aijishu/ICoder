from __future__ import annotations

from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from icoder.cli.main import build_parser, run_cli
from icoder.llm.base import ChatResponse, LlmClient, Message, StreamListener, ToolCall, ToolDefinition


class CliFileLlm(LlmClient):
    def __init__(self) -> None:
        self.turn = 0

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
            return ChatResponse(
                tool_calls=(ToolCall("read", "read_file", '{"path":"note.txt"}'),)
            )
        return ChatResponse(content=f"Observed: {str(messages[-1]['content']).strip()}")

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None,
        listener: StreamListener,
    ) -> ChatResponse:
        return self.chat(messages, tools)


def test_cli_agent_llm_and_tool_registry_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("integration works", encoding="utf-8")
    output = StringIO()
    entries = iter(["Read note.txt", "/exit"])
    args = build_parser().parse_args(["--workspace", str(tmp_path)])

    code = run_cli(
        args,
        input_fn=lambda _prompt: next(entries),
        output=output,
        client_factory=lambda provider=None, *, model=None: CliFileLlm(),
        mcp_config_loader=lambda: (),
    )

    assert code == 0
    assert "Observed: integration works" in output.getvalue()
