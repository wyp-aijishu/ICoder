"""Registry and execution boundary for ICoder tools."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from icoder.tools.base import Tool, ToolError, ToolResult


class ToolRegistry:
    """Store tool definitions and normalize all execution failures."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Register a tool, rejecting invalid or duplicate names."""
        name = tool.name.strip()
        if not name:
            raise ValueError("tool name cannot be empty")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool

    def definitions(self) -> list[dict[str, Any]]:
        """Return deterministic OpenAI-compatible tool definitions."""
        return [self._tools[name].definition() for name in sorted(self._tools)]

    def execute(self, name: str, arguments_json: str | None) -> ToolResult:
        """Execute one tool without allowing expected errors to escape."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, content=f"unknown tool: {name}", is_error=True)

        try:
            arguments = json.loads(arguments_json or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            return ToolResult(
                name=name,
                content=f"invalid tool arguments JSON: {exc}",
                is_error=True,
            )
        if not isinstance(arguments, dict):
            return ToolResult(
                name=name,
                content="tool arguments must be a JSON object",
                is_error=True,
            )

        try:
            content = tool.handler(arguments)
            return ToolResult(name=name, content=str(content))
        except ToolError as exc:
            return ToolResult(name=name, content=str(exc), is_error=True)
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                name=name,
                content=f"invalid arguments: {exc}",
                is_error=True,
            )
        except Exception as exc:  # pragma: no cover - last-resort model boundary
            return ToolResult(
                name=name,
                content=f"tool execution failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def create_default_registry(
    workspace: str | Path,
    *,
    command_timeout_seconds: float = 60.0,
) -> ToolRegistry:
    """Create the built-in registry for a workspace."""
    from icoder.tools.code_search_tool import create_code_search_tools
    from icoder.tools.command_tool import create_command_tools
    from icoder.tools.file_tools import create_file_tools
    from icoder.tools.web_tools import create_web_tools

    registry = ToolRegistry()
    for tool in (
        *create_file_tools(workspace),
        *create_command_tools(workspace, timeout_seconds=command_timeout_seconds),
        *create_code_search_tools(workspace),
        *create_web_tools(),
    ):
        registry.register(tool)
    return registry
