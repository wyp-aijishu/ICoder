from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from icoder.tools.command_tool import create_command_tools
from icoder.tools.registry import ToolRegistry


def registry_for(
    workspace: Path,
    *,
    timeout: float = 2.0,
    max_output: int = 8_000,
) -> ToolRegistry:
    return ToolRegistry(
        create_command_tools(
            workspace,
            timeout_seconds=timeout,
            max_output_chars=max_output,
        )
    )


def python_command(source: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", source])


def test_command_runs_in_workspace_and_reports_exit_code(tmp_path: Path) -> None:
    result = registry_for(tmp_path).execute(
        "execute_command",
        json.dumps({"command": python_command("from pathlib import Path; print(Path.cwd().name)")}),
    )

    assert not result.is_error
    assert "exit code: 0" in result.content
    assert tmp_path.name in result.content


def test_command_timeout_terminates_process(tmp_path: Path) -> None:
    result = registry_for(tmp_path, timeout=0.1).execute(
        "execute_command",
        json.dumps({"command": python_command("import time; time.sleep(10)")}),
    )

    assert not result.is_error
    assert "timed out" in result.content
    assert "exceeded 0.1 seconds" in result.content


def test_command_output_is_truncated(tmp_path: Path) -> None:
    result = registry_for(tmp_path, max_output=300).execute(
        "execute_command",
        json.dumps({"command": python_command("print('x' * 2000)")}),
    )

    assert len(result.content) <= 300
    assert result.content.endswith("...[output truncated]")


def test_obviously_destructive_command_is_rejected(tmp_path: Path) -> None:
    result = registry_for(tmp_path).execute(
        "execute_command", json.dumps({"command": "shutdown /s /t 0"})
    )

    assert result.is_error
    assert "safety policy" in result.content
