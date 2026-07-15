"""Project-isolated Markdown storage for long-term memories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from icoder.tools.base import Tool, ToolError, object_schema

INDEX_FILE_NAME = "MEMORY.md"
MAX_LOADED_INDEX_LINES = 200
MAX_MEMORY_CHARS = 24_000
_INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class MemoryType(str, Enum):
    USER = "user"
    PROJECT = "project"
    CORRECTION = "correction"
    RESOURCE = "resource"


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    type: MemoryType
    name: str
    description: str
    content: str

    @property
    def filename(self) -> str:
        return f"[{self.type.value}]{sanitize_memory_name(self.name)}.md"


class LongTermMemoryStore:
    """Persist memories under a stable hash of the canonical workspace path."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        memory_root: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {self.workspace}")
        configured_root = memory_root or os.getenv("ICODER_MEMORY_ROOT")
        self.root = Path(configured_root or Path.home() / ".icoder" / "memory").expanduser().resolve()
        self.project_id = project_directory_hash(self.workspace)
        self.project_dir = self.root / self.project_id
        self.index_path = self.project_dir / INDEX_FILE_NAME
        self._lock = threading.RLock()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.touch(exist_ok=True)
        self._loaded_index = self._load_index(MAX_LOADED_INDEX_LINES)

    @property
    def index_entries(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(self._loaded_index.items())

    @property
    def index_prompt(self) -> str:
        entries = self.index_entries
        if not entries:
            body = "当前项目还没有长期记忆。"
        else:
            body = "\n".join(f"- {name}: {description}" for name, description in entries)
        return (
            "## 项目长期记忆索引\n\n"
            "以下仅为当前项目的记忆索引。需要查看完整内容时，请调用 `read_memory`，"
            "不要根据索引猜测未记录的细节。\n\n"
            f"{body}"
        )

    def save(self, entry: MemoryEntry) -> Path:
        """Atomically write one memory and upsert its index line."""
        validated = validate_memory_entry(entry)
        with self._lock:
            target = self.project_dir / validated.filename
            _atomic_write(target, _render_memory(validated))
            all_entries = self._load_index(None)
            all_entries[validated.filename] = validated.description
            index_content = "".join(
                f"{name}: {description}\n" for name, description in all_entries.items()
            )
            _atomic_write(self.index_path, index_content)
            self._loaded_index = dict(list(all_entries.items())[:MAX_LOADED_INDEX_LINES])
            return target

    def read(self, name: str) -> str:
        """Read only a memory exposed by the loaded project index."""
        requested = str(name).strip()
        with self._lock:
            if requested not in self._loaded_index:
                raise ToolError("memory is not present in the loaded project index")
            target = (self.project_dir / requested).resolve()
            if target.parent != self.project_dir.resolve() or not target.is_file():
                raise ToolError("memory file does not exist or escapes the project memory directory")
            content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_MEMORY_CHARS:
            return content[:MAX_MEMORY_CHARS] + "\n...[truncated]"
        return content

    def create_read_tool(self) -> Tool:
        def read_memory(arguments: Mapping[str, Any]) -> str:
            name = arguments.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ToolError("name must be a non-empty string")
            return self.read(name)

        return Tool(
            name="read_memory",
            description=(
                "Read the complete content of one long-term memory from the current "
                "project. Use an exact file name from the project memory index."
            ),
            parameters=object_schema(
                {
                    "name": {
                        "type": "string",
                        "description": "Exact indexed memory file name, for example [project]测试命令.md.",
                    }
                },
                ("name",),
            ),
            handler=read_memory,
        )

    def _load_index(self, limit: int | None) -> dict[str, str]:
        entries: dict[str, str] = {}
        with self.index_path.open("r", encoding="utf-8", errors="replace") as stream:
            for line_number, raw_line in enumerate(stream):
                if limit is not None and line_number >= limit:
                    break
                line = raw_line.strip()
                if not line or ": " not in line:
                    continue
                name, description = line.split(": ", 1)
                if name and description:
                    entries[name] = description
        return entries


def project_directory_hash(workspace: str | Path) -> str:
    normalized = Path(workspace).expanduser().resolve().as_posix()
    if os.name == "nt":
        normalized = normalized.casefold()
    normalized = normalized.rstrip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sanitize_memory_name(value: str) -> str:
    name = _INVALID_FILE_CHARS.sub("-", value.strip())
    name = re.sub(r"\s+", " ", name).strip(" .-")
    if not name:
        raise ValueError("memory name cannot be empty")
    return name[:80].rstrip(" .")


def validate_memory_entry(entry: MemoryEntry) -> MemoryEntry:
    name = sanitize_memory_name(entry.name)
    description = " ".join(entry.description.strip().splitlines())
    content = entry.content.strip()
    if not description:
        raise ValueError("memory description cannot be empty")
    if not content:
        raise ValueError("memory content cannot be empty")
    if len(description) > 300:
        raise ValueError("memory description cannot exceed 300 characters")
    if len(content) > MAX_MEMORY_CHARS:
        raise ValueError(f"memory content cannot exceed {MAX_MEMORY_CHARS} characters")
    return MemoryEntry(entry.type, name, description, content)


def _render_memory(entry: MemoryEntry) -> str:
    return (
        "---\n"
        f"type: {entry.type.value}\n"
        f"name: {json.dumps(entry.name, ensure_ascii=False)}\n"
        f"description: {json.dumps(entry.description, ensure_ascii=False)}\n"
        "---\n\n"
        "## Content\n\n"
        f"{entry.content}\n"
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)