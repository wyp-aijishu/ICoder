from __future__ import annotations

import json
from pathlib import Path

import pytest

from icoder.tools.code_search_tool import create_code_search_tools
from icoder.tools.registry import ToolRegistry


def registry_for(workspace: Path) -> ToolRegistry:
    return ToolRegistry(create_code_search_tools(workspace))


def test_literal_search_returns_relative_file_and_line(tmp_path: Path) -> None:
    source = tmp_path / "src" / "sample.py"
    source.parent.mkdir()
    source.write_text("first\nneedle = 1\n", encoding="utf-8")

    result = registry_for(tmp_path).execute(
        "search_code", json.dumps({"query": "needle", "glob": "*.py"})
    )

    assert not result.is_error
    assert "src/sample.py:2: needle = 1" in result.content


def test_regex_search_and_result_limit(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("item1\nitem2\nitem3\n", encoding="utf-8")

    result = registry_for(tmp_path).execute(
        "search_code",
        json.dumps({"query": r"item\d", "regex": True, "max_results": 2}),
    )

    assert result.content.count("sample.txt:") == 2
    assert "truncated" in result.content


def test_invalid_regex_is_a_tool_error(tmp_path: Path) -> None:
    result = registry_for(tmp_path).execute(
        "search_code", json.dumps({"query": "[", "regex": True})
    )

    assert result.is_error
    assert "invalid regular expression" in result.content


def test_search_ignores_dependencies_and_binary_files(tmp_path: Path) -> None:
    dependency = tmp_path / "node_modules"
    dependency.mkdir()
    (dependency / "ignored.js").write_text("needle", encoding="utf-8")
    (tmp_path / "binary.dat").write_bytes(b"needle\x00content")
    (tmp_path / "visible.txt").write_text("needle", encoding="utf-8")

    result = registry_for(tmp_path).execute("search_code", '{"query":"needle"}')

    assert "visible.txt:1" in result.content
    assert "ignored.js" not in result.content
    assert "binary.dat" not in result.content


def test_search_rejects_workspace_escape(tmp_path: Path) -> None:
    result = registry_for(tmp_path).execute(
        "search_code", json.dumps({"query": "x", "path": ".."})
    )

    assert result.is_error
    assert "parent path segments" in result.content


def test_search_skips_file_symlink_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("private-needle", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this system")

    result = registry_for(workspace).execute(
        "search_code", json.dumps({"query": "private-needle"})
    )

    assert "linked.txt" not in result.content
