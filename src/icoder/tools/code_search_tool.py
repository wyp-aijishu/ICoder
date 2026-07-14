"""Cross-platform, bounded source-code search tool."""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from icoder.tools.base import Tool, ToolError, WorkspaceGuard, object_schema

EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "node_modules", "dist", "build"}
)
MAX_FILE_BYTES = 1_000_000
MAX_RESULTS = 200
DEFAULT_RESULTS = 50
MAX_OUTPUT_CHARS = 24_000


def create_code_search_tools(workspace: str | Path) -> tuple[Tool, ...]:
    """Create a code search tool bound to one workspace."""
    guard = WorkspaceGuard(workspace)

    def search_code(arguments: Mapping[str, Any]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise ToolError("query must be a non-empty string")
        root = guard.resolve(arguments.get("path", "."))
        if not root.is_dir():
            raise ToolError("search path is not a directory")
        glob_pattern = arguments.get("glob", "*")
        if not isinstance(glob_pattern, str) or not glob_pattern:
            raise ToolError("glob must be a non-empty string")
        regex = arguments.get("regex", False)
        if not isinstance(regex, bool):
            raise ToolError("regex must be a boolean")
        max_results = _bounded_results(arguments.get("max_results", DEFAULT_RESULTS))

        try:
            matcher = re.compile(query) if regex else None
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc

        matches: list[str] = []
        scanned_files = 0
        output_chars = 0
        truncated = False
        for file in _iter_files(root):
            try:
                if not file.resolve().is_relative_to(guard.root):
                    continue
            except OSError:
                continue
            relative = file.relative_to(guard.root).as_posix()
            relative_to_search = file.relative_to(root).as_posix()
            if not _matches_glob(relative_to_search, file.name, glob_pattern):
                continue
            try:
                if file.stat().st_size > MAX_FILE_BYTES or _is_binary(file):
                    continue
                scanned_files += 1
                with file.open("r", encoding="utf-8", errors="replace") as stream:
                    for line_number, line in enumerate(stream, start=1):
                        found = bool(matcher.search(line)) if matcher is not None else query in line
                        if not found:
                            continue
                        rendered = f"{relative}:{line_number}: {line.rstrip()}"
                        if output_chars + len(rendered) + 1 > MAX_OUTPUT_CHARS:
                            truncated = True
                            break
                        matches.append(rendered)
                        output_chars += len(rendered) + 1
                        if len(matches) >= max_results:
                            truncated = True
                            break
            except (OSError, UnicodeError):
                continue
            if truncated:
                break

        if not matches:
            return f"no matches found (scanned {scanned_files} text files)"
        result = "\n".join(matches)
        if truncated:
            result += "\n...[search results truncated; narrow query/path/glob]"
        return result

    return (
        Tool(
            name="search_code",
            description="Search text files in the workspace using a literal string or regular expression.",
            parameters=object_schema(
                {
                    "query": {"type": "string", "description": "Literal text or regular expression."},
                    "path": {"type": "string", "description": "Workspace-relative search directory."},
                    "glob": {"type": "string", "description": "File glob, for example *.py or **/*.java."},
                    "regex": {"type": "boolean", "description": "Interpret query as a regular expression."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
                },
                ("query",),
            ),
            handler=search_code,
        ),
    )


def _iter_files(root: Path):
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory for directory in directories if directory not in EXCLUDED_DIRECTORIES
        )
        current_path = Path(current)
        for name in sorted(files):
            yield current_path / name


def _matches_glob(relative: str, name: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    if "/" not in normalized:
        return fnmatch.fnmatch(name, normalized)
    return fnmatch.fnmatch(relative, normalized)


def _is_binary(path: Path) -> bool:
    with path.open("rb") as stream:
        return b"\x00" in stream.read(8_192)


def _bounded_results(value: object) -> int:
    if isinstance(value, bool):
        raise ToolError("max_results must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ToolError("max_results must be an integer") from exc
    if not 1 <= parsed <= MAX_RESULTS:
        raise ToolError(f"max_results must be between 1 and {MAX_RESULTS}")
    return parsed
