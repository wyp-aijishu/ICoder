"""Domain types and workspace safety helpers for local tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

JsonObject: TypeAlias = dict[str, Any]
ToolHandler: TypeAlias = Callable[[Mapping[str, Any]], str]


class ToolError(Exception):
    """A predictable tool failure that can be returned to the model."""


class WorkspacePathError(ToolError):
    """Raised when a path attempts to escape the configured workspace."""


@dataclass(frozen=True, slots=True)
class Tool:
    """A callable tool and its JSON Schema definition."""

    name: str
    description: str
    parameters: JsonObject
    handler: ToolHandler

    def definition(self) -> JsonObject:
        """Return an OpenAI-compatible function tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Normalized output produced by the tool registry."""

    name: str
    content: str
    is_error: bool = False


class WorkspaceGuard:
    """Resolve relative paths while preventing workspace escapes."""

    def __init__(self, workspace: str | Path) -> None:
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"workspace is not a directory: {root}")
        self.root = root

    def resolve(self, value: object = ".") -> Path:
        raw = "." if value is None else str(value).strip()
        if not raw:
            raw = "."
        relative = Path(raw)
        if relative.is_absolute():
            raise WorkspacePathError("absolute paths are not allowed")
        if ".." in relative.parts:
            raise WorkspacePathError("parent path segments are not allowed")

        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspacePathError("path escapes the workspace")
        return candidate


def object_schema(
    properties: JsonObject,
    required: tuple[str, ...] = (),
) -> JsonObject:
    """Build the small JSON Schema subset used by built-in tools."""
    schema: JsonObject = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema
