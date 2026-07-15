"""Minimal tool-calling ReAct agent loop."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from icoder.llm.base import (
    NOOP_STREAM_LISTENER,
    ChatResponse,
    LlmClient,
    StreamListener,
    ToolCall,
)
from icoder.memory import MemoryClientFactory, MemoryEntry, MemoryManager
from icoder.tools.registry import ToolRegistry

DEFAULT_MAX_STEPS = 50
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
        stream_listener: StreamListener | None = None,
        memory_root: str | Path | None = None,
        memory_client_factory: MemoryClientFactory | None = None,
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
        self._memory = MemoryManager(
            self._system_prompt,
            workspace or Path.cwd(),
            memory_root=memory_root,
            memory_client_factory=memory_client_factory,
        )
        if "read_memory" not in self._tool_registry:
            self._tool_registry.register(self._memory.long_term.create_read_tool())
        self._stream_listener = stream_listener or NOOP_STREAM_LISTENER

    @property
    def llm_client(self) -> LlmClient:
        return self._llm_client

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def conversation_history(self) -> tuple[dict[str, Any], ...]:
        """Return a defensive snapshot of the current protocol history."""
        return self._memory.messages

    @property
    def used_tokens(self) -> int:
        return self._memory.used_tokens

    def set_llm_client(self, llm_client: LlmClient) -> None:
        """Switch provider/model without discarding conversation history."""
        self._llm_client = llm_client

    def clear_history(self) -> None:
        """Clear the conversation while retaining the system prompt."""
        self._memory.clear()

    def compact_history(self) -> bool:
        """Compact old completed turns, retaining the latest three in full."""
        return self._memory.compact(self._llm_client, force=True)

    def save_memory(self, content: str) -> tuple[MemoryEntry, ...]:
        """Extract and persist explicit `/save` content."""
        return self._memory.save_explicit(content, self._llm_client)

    def run(self, user_input: str) -> str:
        """Run until the model returns a final response or a guard stops it."""
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("user_input cannot be empty")

        self._memory.append_user(user_input)
        previous_signature: tuple[tuple[str, str], ...] | None = None
        repeated_count = 0
        tool_definitions = self._tool_registry.definitions()

        for _step in range(1, self._max_steps + 1):
            self._memory.prepare_for_llm(self._llm_client, tool_definitions or None)
            self._stream_listener.on_llm_start()
            response = self._llm_client.chat_stream(
                self._memory.messages,
                tool_definitions or None,
                self._stream_listener,
            )
            self._memory.record_usage(response)
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

                self._memory.append(self._assistant_tool_message(response))
                self._execute_tool_calls(response.tool_calls)
                continue

            final_content = response.content.strip()
            if not final_content:
                raise AgentLoopError("model returned no final answer")
            self._memory.append(
                {"role": "assistant", "content": response.content}
            )
            self._memory.schedule_implicit_extraction(self._llm_client)
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
            self._stream_listener.on_tool_start(call)
            result = self._tool_registry.execute(call.name, call.arguments)
            content = f"ERROR: {result.content}" if result.is_error else result.content
            self._stream_listener.on_tool_end(
                call,
                result.content,
                is_error=result.is_error,
            )
            self._memory.append(
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

可调用的工具及其参数 Schema 会随每次请求提供。名称以 `mcp__` 开头的工具来自已配置的本地 MCP Server；使用其完整名称和 Schema 中定义的参数。

浏览器页面分析优先使用 DOM 快照类工具，例如 `mcp__chrome_devtools__take_snapshot`。截图等非文本 MCP 结果暂不支持传入模型上下文。

## Tool Policy

- 当需要操作文件、执行命令或创建项目时，请使用工具调用。
- 使用工具后，根据工具返回结果继续思考下一步行动。
- 同一轮返回多个工具调用时，系统会并行执行；如果工具之间有依赖关系，请分多轮调用。
- 如果需要同时检查多个已知且互不依赖的文件或目录，请在同一轮返回多个 `read_file` / `list_dir` / `search_code` 调用。
- 长期记忆索引只包含摘要；需要其中的完整信息时，使用 `read_memory` 并传入索引里的准确文件名。

'''
    )


