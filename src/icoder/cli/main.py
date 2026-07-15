"""ICoder command-line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
from typing import Any, TextIO

from icoder import __version__
from icoder.agent import Agent, AgentError
from icoder.cli.commands import CommandType, parse_command, parse_model_selection
from icoder.cli.streaming import CliStreamRenderer
from icoder.llm import LlmClientFactory, LlmConfigurationError, LlmError
from icoder.mcp import McpConfigError, McpRuntime, create_mcp_tools, load_mcp_servers
from icoder.tools import ToolRegistry, create_default_registry

HELP_TEXT = """可用命令:
    /model                       显示当前模型
    /model [provider[:model]]    切换 Provider，可选指定模型
    /clear                       清空当前对话历史
    /compact                     压缩较早对话并保留最近 3 轮
    /save [content]              将内容提取并保存为项目长期记忆
    /mcp                         显示 MCP Server 状态
    /help                        显示帮助
    /exit, /quit                 退出
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level command-line parser."""
    parser = argparse.ArgumentParser(
        prog="icoder",
        description="ICoder - a minimal command-line ReAct coding agent",
    )
    parser.add_argument(
        "--provider",
        choices=("deepseek", "glm"),
        default=None,
        help="LLM provider (defaults to ICODER_PROVIDER or deepseek)",
    )
    parser.add_argument(
        "--model",
        help="override the provider's configured model",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="workspace available to local tools (default: current directory)",
    )
    parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=12,
        help="maximum ReAct iterations per request (default: 12)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Parse startup arguments and launch the interactive CLI."""
    args = build_parser().parse_args(argv)
    return run_cli(args)


def run_cli(
    args: argparse.Namespace,
    *,
    input_fn: Any = input,
    output: TextIO = sys.stdout,
    client_factory: Any = LlmClientFactory.create,
    registry_factory: Any = create_default_registry,
    mcp_config_loader: Any = load_mcp_servers,
    mcp_runtime_factory: Any = McpRuntime,
) -> int:
    """Assemble and run the CLI; injectable boundaries keep tests offline."""
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        _print(output, f"❌ 启动失败: 工作区不存在或不是目录: {workspace}")
        return 2

    runtime: Any | None = None
    try:
        llm_client = client_factory(args.provider, model=args.model)
        registry = registry_factory(workspace)
        runtime = _register_mcp_tools(
            workspace,
            registry,
            output,
            mcp_config_loader=mcp_config_loader,
            mcp_runtime_factory=mcp_runtime_factory,
        )
        stream_renderer = CliStreamRenderer(output)
        agent = Agent(
            llm_client,
            registry,
            workspace=workspace,
            max_steps=args.max_steps,
            stream_listener=stream_renderer,
            memory_client_factory=lambda provider, model: client_factory(
                provider,
                model=model,
            ),
        )
    except (LlmConfigurationError, ValueError, OSError) as exc:
        _print(output, f"❌ 启动失败: {exc}")
        return 2

    _print(output, f"ICoder {__version__}")
    _print(output, f"工作区: {workspace}")
    _print(output, _model_status(agent))
    _print(output, "输入 /help 查看命令。")

    try:
        while True:
            try:
                raw = input_fn(_prompt(agent))
            except EOFError:
                _print(output, "\n👋 再见!")
                return 0
            except KeyboardInterrupt:
                _print(output, "\n已取消输入。")
                continue

            text = raw.strip()
            if not text:
                continue
            command = parse_command(text)
            if command.type is CommandType.EXIT:
                _print(output, "👋 再见!")
                return 0
            if command.type is CommandType.HELP:
                _print(output, HELP_TEXT.rstrip())
                continue
            if command.type is CommandType.CLEAR:
                agent.clear_history()
                _print(output, "✅ 当前对话历史已清空。")
                continue
            if command.type is CommandType.COMPACT:
                try:
                    compacted = agent.compact_history()
                except (LlmError, ValueError) as exc:
                    _print(output, f"❌ 对话压缩失败: {exc}")
                else:
                    message = "✅ 已压缩较早对话。" if compacted else "当前完整对话不超过 3 轮，无需压缩。"
                    _print(output, message)
                continue
            if command.type is CommandType.SAVE:
                try:
                    entries = agent.save_memory(command.payload or "")
                except (LlmError, ValueError, OSError) as exc:
                    _print(output, f"❌ 长期记忆保存失败: {exc}")
                else:
                    if entries:
                        names = "、".join(entry.filename for entry in entries)
                        _print(output, f"✅ 已保存长期记忆: {names}")
                    else:
                        _print(output, "未发现值得长期保存的内容。")
                continue
            if command.type is CommandType.MCP:
                _print_mcp_status(output, runtime)
                continue
            if command.type is CommandType.UNKNOWN:
                _print(output, f"❌ 未知命令: {command.payload}")
                _print(output, "使用 /help 查看可用命令。")
                continue
            if command.type is CommandType.MODEL:
                if command.payload is None:
                    _print(output, _model_status(agent, prefix="当前模型"))
                    _print(output, "可用 Provider: deepseek, glm")
                    continue
                try:
                    selection = parse_model_selection(command.payload)
                    new_client = client_factory(
                        selection.provider,
                        model=selection.model,
                    )
                except (LlmConfigurationError, ValueError) as exc:
                    _print(output, f"❌ 模型切换失败: {exc}")
                    continue
                agent.set_llm_client(new_client)
                _print(output, f"✅ {_model_status(agent, prefix='已切换模型')}")
                _print(output, "对话历史已保留；使用 /clear 可清空。")
                continue

            try:
                stream_renderer.reset_turn()
                answer = agent.run(text)
            except KeyboardInterrupt:
                _print(output, "\n⏹️ 当前任务已中断。")
            except (LlmError, AgentError) as exc:
                _print(output, f"❌ 任务失败: {exc}")
            else:
                if not stream_renderer.streamed_content:
                    _print(output, answer)
                else:
                    _print(output, "")
    finally:
        if runtime is not None:
            runtime.close()


def _register_mcp_tools(
    workspace: Path,
    registry: ToolRegistry,
    output: TextIO,
    *,
    mcp_config_loader: Any,
    mcp_runtime_factory: Any,
) -> Any | None:
    """Start configured servers and add their discovered tools to the registry."""
    try:
        servers = mcp_config_loader()
    except McpConfigError as exc:
        _print(output, f"⚠ MCP 配置已忽略: {exc}")
        return None
    if not servers:
        return None

    runtime = mcp_runtime_factory(workspace, servers)
    runtime.start()
    for name, message in runtime.failures.items():
        _print(output, f"⚠ MCP 服务 '{name}' 未启用: {message}")
    for server in servers:
        definitions = runtime.tools_for(server.name)
        try:
            tools = create_mcp_tools(server.name, definitions, runtime)
            for tool in tools:
                registry.register(tool)
        except ValueError as exc:
            _print(output, f"⚠ MCP 服务 '{server.name}' 工具已忽略: {exc}")
            continue
        if definitions:
            _print(output, f"✅ MCP 服务 '{server.name}' 已加载 {len(definitions)} 个工具。")
    return runtime


def _print_mcp_status(output: TextIO, runtime: Any | None) -> None:
    """Render the current MCP connection state without touching servers."""
    if runtime is None:
        _print(output, "当前未配置或未连接 MCP Server。")
        return
    statuses = runtime.status()
    if not statuses:
        _print(output, "当前未配置或未连接 MCP Server。")
        return
    _print(output, "MCP Server 状态:")
    for name, connected, tool_count, failure in statuses:
        if connected:
            _print(output, f"  ✅ {name}: 已连接，{tool_count} 个工具")
        else:
            detail = failure or "未连接"
            _print(output, f"  ❌ {name}: 不可用，{detail}")


def _prompt(agent: Agent) -> str:
    client = agent.llm_client
    return f"icoder[{client.provider_name}/{client.model_name}]> "


def _model_status(agent: Agent, *, prefix: str = "模型") -> str:
    client = agent.llm_client
    return f"{prefix}: {client.model_name} ({client.provider_name})"


def _print(output: TextIO, value: str) -> None:
    print(value, file=output, flush=True)
