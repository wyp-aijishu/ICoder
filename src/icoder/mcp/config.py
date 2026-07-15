"""User-level configuration for local stdio MCP servers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_CONFIG_PATH = Path.home() / ".icoder" / "mcp.json"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 60.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 120.0
_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpConfigError(ValueError):
    """Raised when the MCP configuration cannot be used safely."""


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """Validated configuration for one stdio MCP server."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS


def load_mcp_servers(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[McpServerConfig, ...]:
    """Load enabled stdio servers from the user-level MCP configuration."""
    config_path = Path(path or DEFAULT_CONFIG_PATH).expanduser()
    if not config_path.is_file():
        return ()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise McpConfigError(f"cannot read MCP config: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"invalid MCP config JSON: {exc.msg}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        raise McpConfigError("MCP config must contain an 'mcpServers' object")

    environment = os.environ if environ is None else environ
    servers: list[McpServerConfig] = []
    for name, value in data["mcpServers"].items():
        if not isinstance(name, str) or not name.strip():
            raise McpConfigError("MCP server name must be a non-empty string")
        if not isinstance(value, dict):
            raise McpConfigError(f"MCP server '{name}' must be an object")
        if value.get("enabled", True) is False:
            continue
        servers.append(_parse_server(name.strip(), value, environment))
    return tuple(servers)


def _parse_server(
    name: str,
    value: Mapping[str, Any],
    environ: Mapping[str, str],
) -> McpServerConfig:
    command = _required_string(value, "command", name, environ)
    args_value = value.get("args", [])
    if not isinstance(args_value, list) or not all(isinstance(item, str) for item in args_value):
        raise McpConfigError(f"MCP server '{name}' args must be an array of strings")
    env_value = value.get("env", {})
    if not isinstance(env_value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in env_value.items()
    ):
        raise McpConfigError(f"MCP server '{name}' env must map strings to strings")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise McpConfigError(f"MCP server '{name}' enabled must be a boolean")

    return McpServerConfig(
        name=name,
        command=command,
        args=tuple(_expand(item, name, environ) for item in args_value),
        env={key: _expand(item, name, environ) for key, item in env_value.items()},
        startup_timeout_seconds=_timeout(value, "startupTimeoutSeconds", name, DEFAULT_STARTUP_TIMEOUT_SECONDS),
        tool_timeout_seconds=_timeout(value, "toolTimeoutSeconds", name, DEFAULT_TOOL_TIMEOUT_SECONDS),
    )


def _required_string(
    value: Mapping[str, Any], key: str, name: str, environ: Mapping[str, str]
) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise McpConfigError(f"MCP server '{name}' {key} must be a non-empty string")
    return _expand(raw.strip(), name, environ)


def _expand(value: str, name: str, environ: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable == "HOME":
            return str(Path.home())
        replacement = environ.get(variable)
        if replacement is None:
            raise McpConfigError(f"MCP server '{name}' references an unavailable environment variable")
        return replacement

    return _VARIABLE_PATTERN.sub(replace, value)


def _timeout(value: Mapping[str, Any], key: str, name: str, default: float) -> float:
    raw = value.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        raise McpConfigError(f"MCP server '{name}' {key} must be greater than zero")
    return float(raw)