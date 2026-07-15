from __future__ import annotations

from pathlib import Path

import pytest

from icoder.memory.long_term import (
    LongTermMemoryStore,
    MemoryEntry,
    MemoryType,
    project_directory_hash,
)
from icoder.tools.base import ToolError


def test_project_stores_are_isolated_by_canonical_workspace_hash(tmp_path: Path) -> None:
    workspace_one = tmp_path / "one"
    workspace_two = tmp_path / "two"
    workspace_one.mkdir()
    workspace_two.mkdir()
    root = tmp_path / "memories"

    first = LongTermMemoryStore(workspace_one, memory_root=root)
    second = LongTermMemoryStore(workspace_two, memory_root=root)

    assert first.project_id == project_directory_hash(workspace_one)
    assert first.project_dir != second.project_dir
    assert first.index_path.is_file()
    assert second.index_path.is_file()


def test_save_upserts_markdown_file_and_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LongTermMemoryStore(workspace, memory_root=tmp_path / "memory")
    original = MemoryEntry(
        MemoryType.PROJECT,
        "测试:命令",
        "项目使用 pytest 测试",
        "运行 `python -m pytest -q`。",
    )

    target = store.save(original)
    store.save(
        MemoryEntry(
            MemoryType.PROJECT,
            "测试:命令",
            "项目测试命令已确认",
            "运行完整测试。",
        )
    )

    assert target.name == "[project]测试-命令.md"
    assert "type: project" in target.read_text(encoding="utf-8")
    index_lines = store.index_path.read_text(encoding="utf-8").splitlines()
    assert index_lines == ["[project]测试-命令.md: 项目测试命令已确认"]
    assert "项目测试命令已确认" in store.index_prompt


def test_only_first_two_hundred_index_lines_are_loaded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "memory"
    seed = LongTermMemoryStore(workspace, memory_root=root)
    seed.index_path.write_text(
        "".join(f"[project]memory-{index}.md: description {index}\n" for index in range(205)),
        encoding="utf-8",
    )

    loaded = LongTermMemoryStore(workspace, memory_root=root)

    assert len(loaded.index_entries) == 200
    assert "[project]memory-199.md" in dict(loaded.index_entries)
    assert "[project]memory-200.md" not in dict(loaded.index_entries)


def test_read_requires_exact_loaded_index_name(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = LongTermMemoryStore(workspace, memory_root=tmp_path / "memory")
    entry = MemoryEntry(MemoryType.USER, "回复偏好", "偏好中文", "使用简洁中文回复。")
    store.save(entry)

    assert "使用简洁中文回复" in store.read(entry.filename)
    with pytest.raises(ToolError, match="loaded project index"):
        store.read("../secret.md")