"""Pure parser for ICoder interactive slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class CommandType(Enum):
    NONE = auto()
    UNKNOWN = auto()
    MODEL = auto()
    CLEAR = auto()
    HELP = auto()
    EXIT = auto()


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    type: CommandType
    payload: str | None = None


@dataclass(frozen=True, slots=True)
class ModelSelection:
    provider: str
    model: str | None = None


def parse_command(value: str | None) -> ParsedCommand:
    """Parse one input without performing side effects."""
    if value is None:
        return ParsedCommand(CommandType.NONE)
    text = value.strip()
    if not text or not text.startswith("/"):
        return ParsedCommand(CommandType.NONE)

    command, _, payload = text.partition(" ")
    normalized = command.lower()
    normalized_payload = payload.strip() or None
    if normalized in {"/exit", "/quit"} and normalized_payload is None:
        return ParsedCommand(CommandType.EXIT)
    if normalized == "/clear" and normalized_payload is None:
        return ParsedCommand(CommandType.CLEAR)
    if normalized in {"/help", "/?"} and normalized_payload is None:
        return ParsedCommand(CommandType.HELP)
    if normalized == "/model":
        return ParsedCommand(CommandType.MODEL, normalized_payload)
    return ParsedCommand(CommandType.UNKNOWN, text)


def parse_model_selection(payload: str) -> ModelSelection:
    """Parse provider or provider:model syntax used by `/model`."""
    provider, separator, model = payload.partition(":")
    provider = provider.strip()
    if not provider:
        raise ValueError("provider cannot be empty")
    if not separator:
        return ModelSelection(provider=provider)
    model = model.strip()
    if not model:
        raise ValueError("model cannot be empty")
    return ModelSelection(provider=provider, model=model)
