"""Provider-independent language-model contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

Message: TypeAlias = Mapping[str, Any]
ToolDefinition: TypeAlias = Mapping[str, Any]


class LlmError(Exception):
    """Base exception for model invocation failures."""


class LlmConfigurationError(LlmError):
    """Raised when a provider cannot be configured."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A normalized function call requested by a model."""

    id: str
    name: str
    arguments: str

    def as_message_dict(self) -> dict[str, Any]:
        """Serialize the call for an assistant history message."""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """A provider-independent assistant response."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    reasoning_content: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class StreamListener:
    """Receive incremental model output and Agent tool execution events."""

    def on_llm_start(self) -> None:
        """Called immediately before one model request."""

    def on_reasoning_delta(self, delta: str) -> None:
        """Receive an incremental reasoning fragment."""

    def on_content_delta(self, delta: str) -> None:
        """Receive an incremental assistant-content fragment."""

    def on_tool_start(self, call: ToolCall) -> None:
        """Called immediately before a complete tool call is executed."""

    def on_tool_end(self, call: ToolCall, content: str, *, is_error: bool) -> None:
        """Called immediately after a tool call finishes."""


NOOP_STREAM_LISTENER = StreamListener()


class LlmClient(ABC):
    """Strategy interface consumed by the ReAct agent."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the stable provider identifier."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the upstream model identifier."""

    @property
    def max_token(self) -> int:
        """Return the model's maximum context window size."""
        return 1_000_000

    @property
    def preserves_reasoning_content(self) -> bool:
        """Whether assistant reasoning must be returned in request history."""
        return False

    @abstractmethod
    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> ChatResponse:
        """Generate the next assistant response."""

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
        listener: StreamListener | None = None,
    ) -> ChatResponse:
        """Generate a response while emitting deltas.

        Providers without native streaming retain compatibility by invoking
        :meth:`chat` and replaying the complete response as one delta.
        """
        stream = listener or NOOP_STREAM_LISTENER
        response = self.chat(messages, tools)
        if response.reasoning_content:
            stream.on_reasoning_delta(response.reasoning_content)
        if response.content:
            stream.on_content_delta(response.content)
        return response
