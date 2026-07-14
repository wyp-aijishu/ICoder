from __future__ import annotations

import pytest

from icoder.cli.commands import (
    CommandType,
    ModelSelection,
    parse_command,
    parse_model_selection,
)


@pytest.mark.parametrize("value", [None, "", "hello", "  hello  "])
def test_non_commands_are_none(value: str | None) -> None:
    assert parse_command(value).type is CommandType.NONE


def test_parses_supported_commands_case_insensitively() -> None:
    assert parse_command("/MODEL glm").type is CommandType.MODEL
    assert parse_command("/MODEL glm").payload == "glm"
    assert parse_command(" /clear ").type is CommandType.CLEAR
    assert parse_command("/help").type is CommandType.HELP
    assert parse_command("/quit").type is CommandType.EXIT


def test_unknown_and_malformed_commands_are_not_agent_input() -> None:
    assert parse_command("/missing").type is CommandType.UNKNOWN
    assert parse_command("/clear now").type is CommandType.UNKNOWN


def test_parses_model_selection() -> None:
    assert parse_model_selection("deepseek") == ModelSelection("deepseek", None)
    assert parse_model_selection("glm:glm-5.1") == ModelSelection("glm", "glm-5.1")


@pytest.mark.parametrize("payload", ["", ":model", "glm:"])
def test_rejects_incomplete_model_selection(payload: str) -> None:
    with pytest.raises(ValueError):
        parse_model_selection(payload)
