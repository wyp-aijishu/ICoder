"""Minimal tool-calling ReAct agent loop."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from icoder.llm.base import ChatResponse, LlmClient, ToolCall
from icoder.tools.registry import ToolRegistry

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_REPEATED_TOOL_CALLS = 3


class AgentError(Exception):
    """Base exception for agent orchestration failures."""


class AgentLoopError(AgentError):
    """Raised when the model fails to reach a final answer safely."""


class Agent:
    """Maintain conversation history and execute a sequential ReAct loop."""

    def __init__(
        self,
        llm_client: LlmClient,
        tool_registry: ToolRegistry,
        *,
        workspace: str | Path | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_repeated_tool_calls: int = DEFAULT_MAX_REPEATED_TOOL_CALLS,
        system_prompt: str | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be greater than zero")
        if max_repeated_tool_calls < 1:
            raise ValueError("max_repeated_tool_calls must be greater than zero")
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._max_steps = max_steps
        self._max_repeated_tool_calls = max_repeated_tool_calls
        self._system_prompt = system_prompt or _build_system_prompt(workspace)
        self._history: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt}
        ]

    @property
    def llm_client(self) -> LlmClient:
        return self._llm_client

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def conversation_history(self) -> tuple[dict[str, Any], ...]:
        """Return a defensive snapshot of the current protocol history."""
        return tuple(_copy_message(message) for message in self._history)

    def set_llm_client(self, llm_client: LlmClient) -> None:
        """Switch provider/model without discarding conversation history."""
        self._llm_client = llm_client

    def clear_history(self) -> None:
        """Clear the conversation while retaining the system prompt."""
        self._history = [{"role": "system", "content": self._system_prompt}]

    def run(self, user_input: str) -> str:
        """Run until the model returns a final response or a guard stops it."""
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("user_input cannot be empty")

        self._history.append({"role": "user", "content": user_input})
        previous_signature: tuple[tuple[str, str], ...] | None = None
        repeated_count = 0
        tool_definitions = self._tool_registry.definitions()

        for _step in range(1, self._max_steps + 1):
            response = self._llm_client.chat(
                self._history,
                tool_definitions or None,
            )
            if response.has_tool_calls:
                signature = tuple(
                    (call.name, call.arguments) for call in response.tool_calls
                )
                repeated_count = repeated_count + 1 if signature == previous_signature else 1
                previous_signature = signature
                if repeated_count >= self._max_repeated_tool_calls:
                    raise AgentLoopError(
                        "model repeated the same tool calls without making progress"
                    )

                self._history.append(self._assistant_tool_message(response))
                self._execute_tool_calls(response.tool_calls)
                continue

            final_content = response.content.strip()
            if not final_content:
                raise AgentLoopError("model returned no final answer")
            self._history.append(
                {"role": "assistant", "content": response.content}
            )
            return final_content

        raise AgentLoopError(
            f"agent exceeded the maximum of {self._max_steps} ReAct steps"
        )

    def _assistant_tool_message(self, response: ChatResponse) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": response.content or None,
            "tool_calls": [call.as_message_dict() for call in response.tool_calls],
        }
        if (
            response.reasoning_content
            and self._llm_client.preserves_reasoning_content
        ):
            message["reasoning_content"] = response.reasoning_content
        return message

    def _execute_tool_calls(self, calls: Sequence[ToolCall]) -> None:
        for call in calls:
            result = self._tool_registry.execute(call.name, call.arguments)
            content = f"ERROR: {result.content}" if result.is_error else result.content
            self._history.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": content,
                }
            )


def _build_system_prompt(workspace: str | Path | None) -> str:
    root = Path(workspace or Path.cwd()).expanduser().resolve()
    return (
'''
## Identity

你是 ICoder，一个面向代码库工作的智能编程 Agent。

## Language

请用中文回复用户。推理、计划、工具结果解释和最终回复都默认使用中文；只有代码、命令、文件名、API 名称和用户明确要求的外语内容保留原文。

## Tools

你可以使用以下工具：

1. `read_file` - 读取文件内容
2. `write_file` - 写入文件内容
3. `list_dir` - 列出目录内容
4. `execute_command` - 在当前项目目录执行短时 Shell 命令
5. `search_code` - 根据正则表达式搜索代码

## Tool Policy

- 当需要操作文件、执行命令或创建项目时，请使用工具调用。
- 使用工具后，根据工具返回结果继续思考下一步行动。
- 同一轮返回多个工具调用时，系统会并行执行；如果工具之间有依赖关系，请分多轮调用。
- 如果需要同时检查多个已知且互不依赖的文件或目录，请在同一轮返回多个 `read_file` / `list_dir` / `search_code` 调用。

'''
    )


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
