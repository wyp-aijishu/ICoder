"""Bridge MCP tool definitions and results to ICoder's synchronous tool model."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from icoder.tools.base import Tool, ToolError

_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_]+")


class McpToolCaller(Protocol):
    """Synchronous MCP call boundary used by generated handlers."""

    def call_tool(self, server_name: str, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        """Invoke one MCP tool and return its protocol result."""


def create_mcp_tools(
    server_name: str,
    definitions: Iterable[Any],
    caller: McpToolCaller,
) -> tuple[Tool, ...]:
    """Wrap MCP tool definitions as namespaced ICoder tools."""
    tools: list[Tool] = []
    names: set[str] = set()
    for definition in definitions:
        original_name = _attribute(definition, "name")
        if not isinstance(original_name, str) or not original_name.strip():
            raise ValueError(f"MCP server '{server_name}' returned a tool without a name")
        public_name = f"mcp__{_normalize(server_name)}__{_normalize(original_name)}"
        if public_name in names:
            raise ValueError(f"MCP tool name collision: {public_name}")
        names.add(public_name)
        schema = _schema(_attribute(definition, "inputSchema"))
        description = _attribute(definition, "description") or f"MCP tool '{original_name}' from '{server_name}'"

        def handler(
            arguments: Mapping[str, Any],
            *,
            remote_name: str = original_name,
        ) -> str:
            result = caller.call_tool(server_name, remote_name, arguments)
            return format_tool_result(result)

        tools.append(Tool(public_name, str(description), schema, handler))
    return tuple(tools)


def format_tool_result(result: Any) -> str:
    """Convert supported MCP tool content to text without passing binary data onward."""
    if bool(_attribute(result, "isError", "is_error")):
        raise ToolError(_content_text(result) or "MCP tool reported an error")
    text = _content_text(result)
    structured = _attribute(result, "structuredContent", "structured_content")
    if structured is not None:
        encoded = json.dumps(structured, ensure_ascii=False, sort_keys=True, default=str)
        if encoded != text:
            text = "\n".join(part for part in (text, encoded) if part)
    return text or "MCP tool completed without textual content"


def _content_text(result: Any) -> str:
    parts: list[str] = []
    for content in _attribute(result, "content") or ():
        content_type = _attribute(content, "type")
        if content_type == "text":
            parts.append(str(_attribute(content, "text") or ""))
        elif content_type == "resource":
            resource = _attribute(content, "resource")
            resource_text = _attribute(resource, "text")
            if resource_text is None:
                raise ToolError("MCP tool returned an unsupported binary resource")
            parts.append(str(resource_text))
        else:
            raise ToolError("MCP tool returned unsupported non-text content")
    return "\n".join(part for part in parts if part)


def _schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise ValueError("MCP tool inputSchema must be an object schema")
    return {key: item for key, item in value.items() if key != "$schema"}


def _normalize(value: str) -> str:
    normalized = _NAME_PATTERN.sub("_", value.strip()).strip("_").lower()
    if not normalized:
        raise ValueError("MCP server and tool names must contain letters or digits")
    return normalized


def _attribute(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None