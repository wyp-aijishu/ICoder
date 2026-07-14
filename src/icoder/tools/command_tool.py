"""Bounded command execution tool."""

from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from icoder.tools.base import Tool, ToolError, WorkspaceGuard, object_schema

MAX_COMMAND_OUTPUT_CHARS = 8_000

_DANGEROUS_COMMANDS = (
    re.compile(r"(?i)(?:^|[;&|]\s*)rm\s+-[^\r\n]*r[^\r\n]*f[^\r\n]*\s+/(?:\s|$)"),
    re.compile(r"(?i)(?:^|[;&|]\s*)(?:shutdown|reboot|halt|poweroff)(?:\s|$)"),
    re.compile(r"(?i)(?:^|[;&|]\s*)format(?:\.com)?\s+[a-z]:"),
    re.compile(r"(?i)(?:^|[;&|]\s*)(?:del|erase)\s+[^\r\n]*(?:[a-z]:\\|\\\\)[^\r\n]*/s"),
    re.compile(r"(?i)remove-item\s+[^\r\n]*(?:[a-z]:\\|/)[^\r\n]*-recurse"),
)


def create_command_tools(
    workspace: str | Path,
    *,
    timeout_seconds: float = 60.0,
    max_output_chars: int = MAX_COMMAND_OUTPUT_CHARS,
) -> tuple[Tool, ...]:
    """Create a command tool fixed to the supplied working directory."""
    guard = WorkspaceGuard(workspace)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if max_output_chars < 256:
        raise ValueError("max_output_chars must be at least 256")

    def execute_command(arguments: Mapping[str, Any]) -> str:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command must be a non-empty string")
        if any(pattern.search(command) for pattern in _DANGEROUS_COMMANDS):
            raise ToolError("command rejected by the destructive-command safety policy")

        process = _start_process(command, guard.root)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()

        status = "timed out" if timed_out else f"exit code: {process.returncode}"
        sections = [status]
        if stdout:
            sections.append(f"stdout:\n{stdout.rstrip()}")
        if stderr:
            sections.append(f"stderr:\n{stderr.rstrip()}")
        rendered = "\n".join(sections)
        if timed_out:
            rendered += f"\ncommand exceeded {timeout_seconds:g} seconds"
        return _truncate(rendered, max_output_chars)

    return (
        Tool(
            name="execute_command",
            description=(
                "Execute a short shell command in the workspace. Commands have a timeout; "
                "this safety filter is not a sandbox."
            ),
            parameters=object_schema(
                {"command": {"type": "string", "description": "Command interpreted by the system shell."}},
                ("command",),
            ),
            handler=execute_command,
        ),
    )


def _start_process(command: str, cwd: Path) -> subprocess.Popen[str]:
    options: dict[str, Any] = {
        "args": command,
        "cwd": cwd,
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        options["executable"] = os.environ.get("COMSPEC", "cmd.exe")
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["executable"] = "/bin/sh"
        options["start_new_session"] = True
    return subprocess.Popen(**options)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _truncate(value: str, limit: int) -> str:
    marker = "\n...[output truncated]"
    if len(value) <= limit:
        return value
    return value[: max(0, limit - len(marker))] + marker
