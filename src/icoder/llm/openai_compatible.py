"""Shared OpenAI-compatible Chat Completions client."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openai import OpenAI, OpenAIError

from icoder.llm.base import ChatResponse, LlmClient, LlmError, Message, ToolCall, ToolDefinition


class OpenAICompatibleClient(LlmClient):
    """Template strategy for providers implementing OpenAI Chat Completions."""

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        sdk_client: Any | None = None,
    ) -> None:
        self._provider = _required(provider, "provider")
        self._model = _required(model, "model")
        normalized_key = _required(api_key, "api_key")
        normalized_url = _required(base_url, "base_url").rstrip("/")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._sdk_client = sdk_client or OpenAI(
            api_key=normalized_key,
            base_url=normalized_url,
            timeout=timeout,
            max_retries=2,
        )

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
        if not messages:
            raise ValueError("messages cannot be empty")
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
        }
        if tools:
            request["tools"] = [dict(tool) for tool in tools]

        try:
            completion = self._sdk_client.chat.completions.create(**request)
        except OpenAIError as exc:
            raise LlmError(f"{self._provider} API request failed: {exc}") from exc
        except (OSError, TimeoutError) as exc:
            raise LlmError(f"{self._provider} connection failed: {exc}") from exc

        choices = getattr(completion, "choices", None)
        if not choices:
            raise LlmError(f"{self._provider} returned no choices")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise LlmError(f"{self._provider} returned an empty choice")

        content = getattr(message, "content", None) or ""
        reasoning = _optional_text(
            getattr(message, "reasoning_content", None)
            or getattr(message, "reasoning", None)
        )
        tool_calls = tuple(_normalize_tool_call(call) for call in (getattr(message, "tool_calls", None) or ()))
        if not content and not reasoning and not tool_calls:
            raise LlmError(f"{self._provider} returned an empty response")
        usage = getattr(completion, "usage", None)
        prompt_tokens = _usage_value(usage, "prompt_tokens")
        completion_tokens = _usage_value(usage, "completion_tokens")
        total_tokens = _usage_value(usage, "total_tokens")
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )


def _normalize_tool_call(value: Any) -> ToolCall:
    try:
        call_id = _required(getattr(value, "id", None), "tool call id")
    except ValueError as exc:
        raise LlmError(f"provider returned an invalid tool call: {exc}") from exc
    function = getattr(value, "function", None)
    if function is None:
        raise LlmError("provider returned a tool call without a function")
    try:
        name = _required(getattr(function, "name", None), "tool call name")
    except ValueError as exc:
        raise LlmError(f"provider returned an invalid tool call: {exc}") from exc
    arguments = getattr(function, "arguments", None)
    if arguments is None:
        arguments = "{}"
    if not isinstance(arguments, str):
        raise LlmError("provider returned non-string tool arguments")
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _usage_value(usage: object, name: str) -> int:
    value = getattr(usage, name, 0) if usage is not None else 0
    return value if isinstance(value, int) and value >= 0 else 0
