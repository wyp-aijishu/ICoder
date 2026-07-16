from __future__ import annotations

from collections.abc import Sequence
from threading import Barrier, Event, Lock, get_ident
from time import monotonic

import pytest

from icoder.agent.agent import Agent, AgentLoopError
from icoder.llm.base import (
    ChatResponse,
    LlmClient,
    Message,
    StreamListener,
    ToolCall,
    ToolDefinition,
)
from icoder.tools.base import Tool, object_schema
from icoder.tools.registry import ToolRegistry


class ScriptedLlm(LlmClient):
    def __init__(
        self,
        responses: Sequence[ChatResponse],
        *,
        provider: str = "fake",
        preserve_reasoning: bool = False,
    ) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[list[dict], list[dict] | None]] = []
        self._provider = provider
        self._preserve_reasoning = preserve_reasoning

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def preserves_reasoning_content(self) -> bool:
        return self._preserve_reasoning

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> ChatResponse:
        self.requests.append(
            ([dict(message) for message in messages], None if tools is None else [dict(tool) for tool in tools])
        )
        return self.responses.pop(0)

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None,
        listener: StreamListener,
    ) -> ChatResponse:
        response = self.chat(messages, tools)
        if response.reasoning_content:
            listener.on_reasoning_delta(response.reasoning_content)
        if response.content:
            listener.on_content_delta(response.content)
        return response


def echo_registry(executed: list[str] | None = None) -> ToolRegistry:
    def handler(arguments):
        if executed is not None:
            executed.append(arguments["value"])
        return f"observed:{arguments['value']}"

    return ToolRegistry(
        [
            Tool(
                "echo",
                "Echo a value.",
                object_schema({"value": {"type": "string"}}, ("value",)),
                handler,
            )
        ]
    )


def test_react_loop_executes_tool_then_returns_final_answer() -> None:
    caller_thread = get_ident()
    handler_threads: list[int] = []

    def handler(arguments):
        handler_threads.append(get_ident())
        return f"observed:{arguments['value']}"

    llm = ScriptedLlm(
        [
            ChatResponse(tool_calls=(ToolCall("call-1", "echo", '{"value":"hello"}'),)),
            ChatResponse(content="Done."),
        ]
    )
    registry = ToolRegistry(
        [
            Tool(
                "echo",
                "Echo a value.",
                object_schema({"value": {"type": "string"}}, ("value",)),
                handler,
            )
        ]
    )
    agent = Agent(llm, registry)

    answer = agent.run("Use the echo tool")

    assert answer == "Done."
    roles = [message["role"] for message in agent.conversation_history]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert agent.conversation_history[2]["tool_calls"][0]["id"] == "call-1"
    assert agent.conversation_history[3] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "observed:hello",
    }
    assert len(llm.requests) == 2
    assert llm.requests[1][0][-1]["role"] == "tool"
    assert handler_threads == [caller_thread]


def test_multiple_tool_calls_execute_in_parallel_and_preserve_message_order() -> None:
    executed: list[str] = []
    barrier = Barrier(2)

    def handler(arguments):
        barrier.wait(timeout=1)
        executed.append(arguments["value"])
        return f"observed:{arguments['value']}"

    registry = ToolRegistry(
        [
            Tool(
                "parallel_echo",
                "Echo a value after all calls start.",
                object_schema({"value": {"type": "string"}}, ("value",)),
                handler,
            )
        ]
    )
    llm = ScriptedLlm(
        [
            ChatResponse(
                tool_calls=(
                    ToolCall("one", "parallel_echo", '{"value":"first"}'),
                    ToolCall("two", "parallel_echo", '{"value":"second"}'),
                )
            ),
            ChatResponse(content="complete"),
        ]
    )
    agent = Agent(llm, registry)

    agent.run("run both")

    assert sorted(executed) == ["first", "second"]
    tool_messages = [m for m in agent.conversation_history if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["one", "two"]


def test_multiple_tool_calls_use_at_most_three_workers() -> None:
    lock = Lock()
    barrier = Barrier(3)
    active = 0
    peak = 0

    def handler(arguments):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=1)
        with lock:
            active -= 1
        return arguments["value"]

    registry = ToolRegistry(
        [
            Tool(
                "limited",
                "Run with limited concurrency.",
                object_schema({"value": {"type": "string"}}, ("value",)),
                handler,
            )
        ]
    )
    calls = tuple(
        ToolCall(str(index), "limited", f'{{"value":"{index}"}}')
        for index in range(6)
    )
    llm = ScriptedLlm(
        [ChatResponse(tool_calls=calls), ChatResponse(content="complete")]
    )

    Agent(llm, registry).run("run all")

    assert peak == 3


def test_timed_out_tool_returns_error_to_model_without_waiting_for_completion() -> None:
    release = Event()

    def slow_tool(_arguments):
        release.wait(timeout=1)
        return "too late"

    registry = ToolRegistry(
        [
            Tool("slow", "Run slowly.", object_schema({}), slow_tool),
            Tool("quick", "Return immediately.", object_schema({}), lambda _: "done"),
        ]
    )
    llm = ScriptedLlm(
        [
            ChatResponse(
                tool_calls=(
                    ToolCall("slow-call", "slow", "{}"),
                    ToolCall("quick-call", "quick", "{}"),
                )
            ),
            ChatResponse(content="recovered"),
        ]
    )
    agent = Agent(llm, registry, tool_timeout_seconds=0.02)

    started = monotonic()
    assert agent.run("run slowly") == "recovered"
    elapsed = monotonic() - started
    release.set()

    tool_message = agent.conversation_history[-3]
    assert tool_message["content"] == (
        "ERROR: tool execution timed out after 0.02 seconds"
    )
    assert elapsed < 0.3


def test_tool_timeout_must_be_greater_than_zero() -> None:
    with pytest.raises(ValueError, match="tool_timeout_seconds"):
        Agent(ScriptedLlm([]), echo_registry(), tool_timeout_seconds=0)


def test_keyboard_interrupt_during_tool_rolls_back_current_turn() -> None:
    def interrupt(_arguments):
        raise KeyboardInterrupt

    registry = ToolRegistry(
        [
            Tool(
                "interrupt",
                "Interrupt execution.",
                object_schema({}),
                interrupt,
            )
        ]
    )
    llm = ScriptedLlm(
        [
            ChatResponse(content="first answer"),
            ChatResponse(tool_calls=(ToolCall("call", "interrupt", "{}"),)),
        ]
    )
    agent = Agent(llm, registry)
    agent.run("first question")
    history_before_interruption = agent.conversation_history

    with pytest.raises(KeyboardInterrupt):
        agent.run("interrupted question")

    assert agent.conversation_history == history_before_interruption


def test_tool_error_is_returned_to_model_and_loop_continues() -> None:
    llm = ScriptedLlm(
        [
            ChatResponse(tool_calls=(ToolCall("bad", "missing", "{"),)),
            ChatResponse(content="I recovered."),
        ]
    )
    agent = Agent(llm, echo_registry())

    assert agent.run("recover") == "I recovered."
    assert agent.conversation_history[-2]["content"].startswith("ERROR: unknown tool")


def test_clear_history_keeps_only_system_prompt() -> None:
    llm = ScriptedLlm([ChatResponse(content="answer")])
    agent = Agent(llm, echo_registry())
    agent.run("question")

    agent.clear_history()

    assert len(agent.conversation_history) == 1
    assert agent.conversation_history[0]["role"] == "system"


def test_switching_client_preserves_history_and_uses_new_client() -> None:
    first = ScriptedLlm([ChatResponse(content="first")])
    second = ScriptedLlm([ChatResponse(content="second")], provider="second")
    agent = Agent(first, echo_registry())
    agent.run("one")

    agent.set_llm_client(second)
    answer = agent.run("two")

    assert answer == "second"
    assert len(second.requests[0][0]) == 4
    assert agent.llm_client.provider_name == "second"


def test_deepseek_style_reasoning_is_preserved_for_tool_history() -> None:
    llm = ScriptedLlm(
        [
            ChatResponse(
                reasoning_content="reason",
                tool_calls=(ToolCall("call", "echo", '{"value":"x"}'),),
            ),
            ChatResponse(content="done"),
        ],
        preserve_reasoning=True,
    )
    agent = Agent(llm, echo_registry())

    agent.run("question")

    assert agent.conversation_history[2]["reasoning_content"] == "reason"


def test_reasoning_is_not_sent_for_clients_without_capability() -> None:
    llm = ScriptedLlm(
        [
            ChatResponse(
                reasoning_content="private",
                tool_calls=(ToolCall("call", "echo", '{"value":"x"}'),),
            ),
            ChatResponse(content="done"),
        ]
    )
    agent = Agent(llm, echo_registry())

    agent.run("question")

    assert "reasoning_content" not in agent.conversation_history[2]


def test_repeated_identical_tool_calls_are_stopped() -> None:
    repeated = ChatResponse(tool_calls=(ToolCall("id", "echo", '{"value":"x"}'),))
    llm = ScriptedLlm([repeated, repeated, repeated])
    agent = Agent(llm, echo_registry(), max_repeated_tool_calls=3)

    with pytest.raises(AgentLoopError, match="repeated"):
        agent.run("loop")


def test_max_steps_stops_unfinished_loop() -> None:
    calls = [
        ChatResponse(tool_calls=(ToolCall(str(index), "echo", f'{{"value":"{index}"}}'),))
        for index in range(2)
    ]
    agent = Agent(ScriptedLlm(calls), echo_registry(), max_steps=2)

    with pytest.raises(AgentLoopError, match="maximum of 2"):
        agent.run("loop")


def test_empty_final_answer_is_rejected() -> None:
    agent = Agent(ScriptedLlm([ChatResponse()]), echo_registry())

    with pytest.raises(AgentLoopError, match="no final answer"):
        agent.run("question")


def test_empty_user_input_is_rejected_without_history_mutation() -> None:
    agent = Agent(ScriptedLlm([]), echo_registry())

    with pytest.raises(ValueError, match="user_input"):
        agent.run("   ")

    assert len(agent.conversation_history) == 1


def test_history_property_is_a_defensive_copy() -> None:
    agent = Agent(ScriptedLlm([ChatResponse(content="answer")]), echo_registry())
    agent.run("question")
    snapshot = agent.conversation_history

    snapshot[-1]["content"] = "changed"

    assert agent.conversation_history[-1]["content"] == "answer"


def test_stream_listener_receives_model_and_tool_events_in_order() -> None:
    events: list[str] = []

    class Listener(StreamListener):
        def on_llm_start(self) -> None:
            events.append("llm")

        def on_reasoning_delta(self, delta: str) -> None:
            events.append(f"reasoning:{delta}")

        def on_content_delta(self, delta: str) -> None:
            events.append(f"content:{delta}")

        def on_tool_start(self, call: ToolCall) -> None:
            events.append(f"tool-start:{call.name}")

        def on_tool_end(self, call: ToolCall, content: str, *, is_error: bool) -> None:
            events.append(f"tool-end:{call.name}:{is_error}")

    llm = ScriptedLlm([
        ChatResponse(
            reasoning_content="inspect",
            tool_calls=(ToolCall("call", "echo", '{"value":"x"}'),),
        ),
        ChatResponse(content="done"),
    ])
    agent = Agent(llm, echo_registry(), stream_listener=Listener())

    assert agent.run("question") == "done"
    assert events == [
        "llm",
        "reasoning:inspect",
        "tool-start:echo",
        "tool-end:echo:False",
        "llm",
        "content:done",
    ]
