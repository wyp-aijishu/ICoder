"""Terminal rendering for incremental Agent events."""

from __future__ import annotations

from typing import TextIO

from icoder.llm.base import StreamListener, ToolCall


class CliStreamRenderer(StreamListener):
    """Render reasoning, answer text, and tool progress without buffering."""

    def __init__(self, output: TextIO) -> None:
        self._output = output
        self._phase: str | None = None
        self._wrote_anything = False
        self._streamed_content = False

    @property
    def streamed_content(self) -> bool:
        return self._streamed_content

    def reset_turn(self) -> None:
        self._phase = None
        self._wrote_anything = False
        self._streamed_content = False

    def on_llm_start(self) -> None:
        self._phase = None

    def on_reasoning_delta(self, delta: str) -> None:
        if not delta:
            return
        self._enter_phase("reasoning", "🧠 思考: ")
        self._write(delta)

    def on_content_delta(self, delta: str) -> None:
        if not delta:
            return
        self._enter_phase("content", "💬 回复: ")
        self._streamed_content = True
        self._write(delta)

    def on_tool_start(self, call: ToolCall) -> None:
        self._finish_line()
        self._write(f"🔧 调用工具 {call.name}: {call.arguments}\n")
        self._phase = "tool"

    def on_tool_end(self, call: ToolCall, content: str, *, is_error: bool) -> None:
        marker = "❌" if is_error else "✅"
        summary = _single_line(content, 160)
        self._write(f"{marker} {call.name} {'失败' if is_error else '完成'}")
        if summary:
            self._write(f": {summary}")
        self._write("\n")

    def _enter_phase(self, phase: str, heading: str) -> None:
        if self._phase == phase:
            return
        self._finish_line()
        self._write(heading)
        self._phase = phase

    def _finish_line(self) -> None:
        if self._wrote_anything:
            self._write("\n")

    def _write(self, value: str) -> None:
        self._output.write(value)
        self._output.flush()
        self._wrote_anything = True


def _single_line(value: str, limit: int) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."