from __future__ import annotations

from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from icoder.cli.main import build_parser, run_cli
from icoder.llm.base import ChatResponse, LlmClient, LlmConfigurationError, Message, StreamListener, ToolDefinition
from icoder.mcp.config import McpServerConfig


class RecordingLlm(LlmClient):
    def __init__(self, provider: str, model: str, answers: Sequence[str] = ()) -> None:
        self._provider = provider
        self._model = model
        self.answers = list(answers)
        self.requests: list[list[dict]] = []

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> ChatResponse:
        self.requests.append([dict(message) for message in messages])
        return ChatResponse(content=self.answers.pop(0))

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None,
        listener: StreamListener,
    ) -> ChatResponse:
        return self.chat(messages, tools)


class ClientFactory:
    def __init__(self) -> None:
        self.created: list[RecordingLlm] = []

    def __call__(self, provider=None, *, model=None):
        selected = provider or "deepseek"
        if selected == "missing":
            raise LlmConfigurationError("missing provider configuration")
        client = RecordingLlm(selected, model or f"{selected}-default", [f"answer from {selected}"])
        self.created.append(client)
        return client


def inputs(*values: str):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


class RecordingMcpRuntime:
    def __init__(self, _workspace, _servers) -> None:
        self.closed = False

    @property
    def failures(self):
        return {"unavailable": "process exited"}

    def start(self) -> None:
        pass

    def tools_for(self, name: str):
        return ()

    def status(self):
        return (
            ("connected", True, 2, None),
            ("unavailable", False, 0, "process exited"),
        )

    def close(self) -> None:
        self.closed = True


def test_repl_handles_task_commands_model_switch_and_unknown(tmp_path: Path) -> None:
    output = StringIO()
    factory = ClientFactory()
    args = build_parser().parse_args(["--workspace", str(tmp_path), "--provider", "deepseek"])

    code = run_cli(
        args,
        input_fn=inputs(
            "first question",
            "/model",
            "/model glm:glm-custom",
            "second question",
            "/clear",
            "/unknown",
            "/help",
            "/exit",
        ),
        output=output,
        client_factory=factory,
        mcp_config_loader=lambda: (),
    )

    rendered = output.getvalue()
    assert code == 0
    assert "answer from deepseek" in rendered
    assert "当前模型: deepseek-default (deepseek)" in rendered
    assert "已切换模型: glm-custom (glm)" in rendered
    assert "answer from glm" in rendered
    assert "对话历史已清空" in rendered
    assert "未知命令: /unknown" in rendered
    assert "/model [provider[:model]]" in rendered
    assert len(factory.created[1].requests[0]) == 4  # Existing turn was retained on switch.


def test_failed_model_switch_keeps_current_client(tmp_path: Path) -> None:
    output = StringIO()
    factory = ClientFactory()
    args = build_parser().parse_args(["--workspace", str(tmp_path)])

    code = run_cli(
        args,
        input_fn=inputs("/model missing", "question", "/exit"),
        output=output,
        client_factory=factory,
        mcp_config_loader=lambda: (),
    )

    assert code == 0
    assert "模型切换失败: missing provider configuration" in output.getvalue()
    assert "answer from deepseek" in output.getvalue()
    assert len(factory.created) == 1


def test_startup_configuration_error_returns_two(tmp_path: Path) -> None:
    output = StringIO()
    args = build_parser().parse_args(["--workspace", str(tmp_path)])

    def failing_factory(provider=None, *, model=None):
        raise LlmConfigurationError("missing API key")

    code = run_cli(
        args,
        input_fn=inputs(),
        output=output,
        client_factory=failing_factory,
        mcp_config_loader=lambda: (),
    )

    assert code == 2
    assert "启动失败: missing API key" in output.getvalue()


def test_invalid_workspace_returns_two(tmp_path: Path) -> None:
    output = StringIO()
    args = build_parser().parse_args(["--workspace", str(tmp_path / "missing")])

    code = run_cli(args, input_fn=inputs(), output=output, client_factory=ClientFactory())

    assert code == 2
    assert "工作区不存在" in output.getvalue()


def test_eof_exits_cleanly(tmp_path: Path) -> None:
    output = StringIO()
    args = build_parser().parse_args(["--workspace", str(tmp_path)])

    def eof(_prompt: str) -> str:
        raise EOFError

    assert run_cli(
        args,
        input_fn=eof,
        output=output,
        client_factory=ClientFactory(),
        mcp_config_loader=lambda: (),
    ) == 0
    assert "再见" in output.getvalue()


def test_mcp_command_reports_unconfigured_and_server_status(tmp_path: Path) -> None:
    output = StringIO()
    args = build_parser().parse_args(["--workspace", str(tmp_path)])

    code = run_cli(
        args,
        input_fn=inputs("/mcp", "/exit"),
        output=output,
        client_factory=ClientFactory(),
        mcp_config_loader=lambda: (),
    )

    assert code == 0
    assert "当前未配置或未连接 MCP Server" in output.getvalue()

    output = StringIO()
    code = run_cli(
        args,
        input_fn=inputs("/mcp", "/exit"),
        output=output,
        client_factory=ClientFactory(),
        mcp_config_loader=lambda: (McpServerConfig("connected", "fake"),),
        mcp_runtime_factory=RecordingMcpRuntime,
    )

    rendered = output.getvalue()
    assert code == 0
    assert "connected: 已连接，2 个工具" in rendered
    assert "unavailable: 不可用，process exited" in rendered
