from __future__ import annotations

import json

import pytest

from icoder.mcp.config import McpConfigError, load_mcp_servers


def test_missing_config_means_no_servers(tmp_path) -> None:
    assert load_mcp_servers(tmp_path / "missing.json") == ()


def test_loads_enabled_server_and_expands_environment(tmp_path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "chrome-devtools": {
                        "command": "${RUNNER}",
                        "args": ["--token=${TOKEN}"],
                        "env": {"LOG_DIR": "${HOME}/logs"},
                        "startupTimeoutSeconds": 30,
                        "toolTimeoutSeconds": 45,
                    },
                    "disabled": {"command": "ignored", "enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )

    servers = load_mcp_servers(config, environ={"RUNNER": "npx", "TOKEN": "value"})

    assert len(servers) == 1
    assert servers[0].name == "chrome-devtools"
    assert servers[0].command == "npx"
    assert servers[0].args == ("--token=value",)
    assert servers[0].startup_timeout_seconds == 30
    assert servers[0].tool_timeout_seconds == 45
    assert servers[0].env is not None
    assert servers[0].env["LOG_DIR"].endswith("logs")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "mcpServers"),
        ({"mcpServers": {"server": {"command": "npx", "args": "bad"}}}, "args"),
        ({"mcpServers": {"server": {"command": "npx", "toolTimeoutSeconds": 0}}}, "toolTimeoutSeconds"),
    ],
)
def test_rejects_invalid_server_configuration(tmp_path, payload, message: str) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(McpConfigError, match=message):
        load_mcp_servers(config)


def test_missing_environment_variable_does_not_leak_value(tmp_path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"server": {"command": "${MISSING}"}}}),
        encoding="utf-8",
    )

    with pytest.raises(McpConfigError, match="unavailable environment variable"):
        load_mcp_servers(config, environ={})