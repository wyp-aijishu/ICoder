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
from icoder.tools import create_default_registry

HELP_TEXT = """可用命令:
    /model                       显示当前模型
    /model [provider[:model]]    切换 Provider，可选指定模型
    /clear                       清空当前对话历史
    /compact                     压缩较早对话并保留最近 3 轮
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
) -> int:
    """Assemble and run the CLI; injectable boundaries keep tests offline."""
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        _print(output, f"❌ 启动失败: 工作区不存在或不是目录: {workspace}")
        return 2

    try:
        llm_client = client_factory(args.provider, model=args.model)
        registry = registry_factory(workspace)
        stream_renderer = CliStreamRenderer(output)
        agent = Agent(
            llm_client,
            registry,
            workspace=workspace,
            max_steps=args.max_steps,
            stream_listener=stream_renderer,
        )
    except (LlmConfigurationError, ValueError, OSError) as exc:
        _print(output, f"❌ 启动失败: {exc}")
        return 2

    _print(output, f"ICoder {__version__}")
    _print(output, f"工作区: {workspace}")
    _print(output, _model_status(agent))
    _print(output, "输入 /help 查看命令。")

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


def _prompt(agent: Agent) -> str:
    client = agent.llm_client
    return f"icoder[{client.provider_name}/{client.model_name}]> "


def _model_status(agent: Agent, *, prefix: str = "模型") -> str:
    client = agent.llm_client
    return f"{prefix}: {client.model_name} ({client.provider_name})"


def _print(output: TextIO, value: str) -> None:
    print(value, file=output, flush=True)
