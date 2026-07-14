"""Workspace-scoped file tools."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from icoder.tools.base import Tool, ToolError, WorkspaceGuard, object_schema

MAX_READ_LINES = 2_000
DEFAULT_READ_LINES = 200
MAX_READ_CHARS = 24_000
MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_LIST_RESULTS = 200


def create_file_tools(workspace: str | Path) -> tuple[Tool, ...]:
    """Create file tools bound to one workspace root."""
    guard = WorkspaceGuard(workspace)

    def read_file(arguments: Mapping[str, Any]) -> str:
        path = guard.resolve(_required(arguments, "path"))
        if not path.is_file():
            raise ToolError("file does not exist or is not a regular file")

        offset = _integer(arguments.get("offset", 1), "offset", minimum=1)
        limit = _integer(
            arguments.get("limit", DEFAULT_READ_LINES),
            "limit",
            minimum=1,
            maximum=MAX_READ_LINES,
        )
        selected: list[str] = []
        truncated = False
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line_number, line in enumerate(stream, start=1):
                if line_number < offset:
                    continue
                if len(selected) >= limit:
                    truncated = True
                    break
                selected.append(line)

        if not selected and offset > 1:
            raise ToolError(f"offset {offset} is beyond the end of the file")
        content = "".join(selected)
        if len(content) > MAX_READ_CHARS:
            content = content[:MAX_READ_CHARS]
            truncated = True
        if truncated:
            content += "\n...[truncated; use offset/limit to continue]"
        return content

    def write_file(arguments: Mapping[str, Any]) -> str:
        path = guard.resolve(_required(arguments, "path"))
        content = _required(arguments, "content")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        size = len(content.encode("utf-8"))
        if size > MAX_WRITE_BYTES:
            raise ToolError(f"content exceeds the {MAX_WRITE_BYTES}-byte write limit")
        if path.exists() and not path.is_file():
            raise ToolError("target exists and is not a regular file")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        relative = path.relative_to(guard.root).as_posix()
        return f"wrote {size} bytes to {relative}"

    def list_dir(arguments: Mapping[str, Any]) -> str:
        path = guard.resolve(arguments.get("path", "."))
        if not path.is_dir():
            raise ToolError("directory does not exist")
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        lines: list[str] = []
        for entry in entries[:MAX_LIST_RESULTS]:
            kind = "[D]" if entry.is_dir() else "[F]"
            lines.append(f"{kind} {entry.name}")
        if len(entries) > MAX_LIST_RESULTS:
            lines.append(f"...[truncated after {MAX_LIST_RESULTS} entries]")
        return "\n".join(lines) if lines else "directory is empty"

    return (
        Tool(
            name="read_file",
            description="Read a UTF-8 text file inside the workspace by line range.",
            parameters=object_schema(
                {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "offset": {"type": "integer", "minimum": 1, "description": "First line, starting at 1."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_READ_LINES},
                },
                ("path",),
            ),
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="Write UTF-8 text to a file inside the workspace.",
            parameters=object_schema(
                {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "content": {"type": "string", "description": "Complete file content."},
                },
                ("path", "content"),
            ),
            handler=write_file,
        ),
        Tool(
            name="list_dir",
            description="List files and directories inside the workspace.",
            parameters=object_schema(
                {"path": {"type": "string", "description": "Workspace-relative directory; defaults to ."}}
            ),
            handler=list_dir,
        ),
    )


def _required(arguments: Mapping[str, Any], name: str) -> Any:
    if name not in arguments:
        raise ToolError(f"missing required argument: {name}")
    return arguments[name]


def _integer(
    value: object,
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise ToolError(f"{name} must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{name} must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise ToolError(f"{name} must be at least {minimum}{upper}")
    return parsed
