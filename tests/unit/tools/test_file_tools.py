from __future__ import annotations

import json
from pathlib import Path

import pytest

from icoder.tools.registry import ToolRegistry
from icoder.tools.file_tools import create_file_tools


def registry_for(workspace: Path) -> ToolRegistry:
    return ToolRegistry(create_file_tools(workspace))


def test_write_read_and_list_files(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    written = registry.execute(
        "write_file",
        json.dumps({"path": "src/example.txt", "content": "one\ntwo\nthree\n"}),
    )
    read = registry.execute(
        "read_file",
        json.dumps({"path": "src/example.txt", "offset": 2, "limit": 1}),
    )
    listed = registry.execute("list_dir", json.dumps({"path": "src"}))

    assert not written.is_error
    assert read.content.startswith("two\n")
    assert "[truncated" in read.content
    assert "[F] example.txt" in listed.content


@pytest.mark.parametrize("path", ["../secret.txt", "nested/../../secret.txt"])
def test_file_tools_reject_parent_path_escape(tmp_path: Path, path: str) -> None:
    registry = registry_for(tmp_path)

    result = registry.execute("read_file", json.dumps({"path": path}))

    assert result.is_error
    assert "parent path segments" in result.content


def test_file_tools_reject_absolute_paths(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)

    result = registry.execute("read_file", json.dumps({"path": str(tmp_path / "file.txt")}))

    assert result.is_error
    assert "absolute paths" in result.content


def test_file_tools_reject_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this system")

    result = registry_for(workspace).execute(
        "read_file", json.dumps({"path": "link/secret.txt"})
    )

    assert result.is_error
    assert "escapes the workspace" in result.content


def test_read_file_truncates_large_content(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 30_000, encoding="utf-8")

    result = registry_for(tmp_path).execute("read_file", '{"path":"large.txt"}')

    assert not result.is_error
    assert "[truncated" in result.content
    assert len(result.content) < 25_000
