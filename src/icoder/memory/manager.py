"""Unified facade for short-term and project-scoped long-term memory."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from icoder.llm.base import ChatResponse, LlmClient, Message, ToolDefinition
from icoder.memory.extractor import MemoryExtractor
from icoder.memory.long_term import LongTermMemoryStore, MemoryEntry
from icoder.memory.short_term import ShortTermMemory, ShortTermMemoryCheckpoint

MemoryClientFactory = Callable[[str, str], LlmClient]
IMPLICIT_EXTRACTION_EVENT_THRESHOLD = 4


@dataclass(frozen=True, slots=True)
class MemoryCheckpoint:
    """Opaque snapshot of mutable memory state."""

    short_term: ShortTermMemoryCheckpoint
    pending_events: tuple[dict[str, Any], ...]


class MemoryManager:
    """Expose one API for history, compaction, persistence, and extraction."""

    def __init__(
        self,
        system_prompt: str,
        workspace: str | Path,
        *,
        memory_root: str | Path | None = None,
        memory_client_factory: MemoryClientFactory | None = None,
    ) -> None:
        self._base_system_prompt = system_prompt
        self.long_term = LongTermMemoryStore(workspace, memory_root=memory_root)
        self.short_term = ShortTermMemory(self._prompt_with_index())
        self._extractor = MemoryExtractor()
        self._memory_client_factory = memory_client_factory
        self._pending_events: list[dict[str, Any]] = []
        self._event_lock = threading.Lock()

    @property
    def messages(self) -> tuple[dict[str, Any], ...]:
        return self.short_term.messages

    @property
    def used_tokens(self) -> int:
        return self.short_term.used_tokens

    def append_user(self, content: str) -> None:
        self.short_term.append_user(content)
        self._record_event({"role": "user", "content": content})

    def append(self, message: dict[str, Any]) -> None:
        self.short_term.append(message)
        self._record_event(message)

    def record_usage(self, response: ChatResponse) -> None:
        self.short_term.record_usage(response)

    def prepare_for_llm(
        self,
        llm_client: LlmClient,
        tools: Sequence[ToolDefinition] | None = None,
    ) -> bool:
        return self.short_term.prepare_for_llm(llm_client, tools)

    def compact(self, llm_client: LlmClient, *, force: bool = False) -> bool:
        return self.short_term.compact(llm_client, force=force)

    def clear(self) -> None:
        self.short_term.clear()
        with self._event_lock:
            self._pending_events.clear()

    def checkpoint(self) -> MemoryCheckpoint:
        with self._event_lock:
            pending_events = tuple(
                _copy_event(event) for event in self._pending_events
            )
        return MemoryCheckpoint(self.short_term.checkpoint(), pending_events)

    def restore(self, checkpoint: MemoryCheckpoint) -> None:
        self.short_term.restore(checkpoint.short_term)
        with self._event_lock:
            self._pending_events = [
                _copy_event(event) for event in checkpoint.pending_events
            ]

    def save_explicit(self, content: str, llm_client: LlmClient) -> tuple[MemoryEntry, ...]:
        entries = self._extractor.extract_text(content, llm_client)
        if not entries:
            return ()
        for entry in entries:
            self.long_term.save(entry)
        self.short_term.set_base_system_prompt(self._prompt_with_index())
        return entries

    def schedule_implicit_extraction(self, llm_client: LlmClient) -> bool:
        """Consume four or more events and extract in a daemon background thread."""
        if self._memory_client_factory is None:
            return False
        with self._event_lock:
            if len(self._pending_events) < IMPLICIT_EXTRACTION_EVENT_THRESHOLD:
                return False
            snapshot = tuple(dict(event) for event in self._pending_events)
            self._pending_events.clear()
        provider = llm_client.provider_name
        model = llm_client.model_name
        thread = threading.Thread(
            target=self._extract_implicit,
            args=(snapshot, provider, model),
            name="icoder-memory-extractor",
            daemon=True,
        )
        thread.start()
        return True

    def _extract_implicit(
        self,
        events: Sequence[Message],
        provider: str,
        model: str,
    ) -> None:
        try:
            assert self._memory_client_factory is not None
            client = self._memory_client_factory(provider, model)
            entries = self._extractor.extract_messages(events, client)
            for entry in entries:
                self.long_term.save(entry)
        except Exception:
            # Background extraction must never break or delay the foreground task.
            return

    def _record_event(self, message: dict[str, Any]) -> None:
        with self._event_lock:
            self._pending_events.append(_copy_event(message))

    def _prompt_with_index(self) -> str:
        return f"{self._base_system_prompt.rstrip()}\n\n{self.long_term.index_prompt}\n"


def _copy_event(message: dict[str, Any]) -> dict[str, Any]:
    copied = dict(message)
    if "tool_calls" in copied:
        copied["tool_calls"] = [
            {**dict(call), "function": dict(call.get("function", {}))}
            for call in copied["tool_calls"]
        ]
    return copied