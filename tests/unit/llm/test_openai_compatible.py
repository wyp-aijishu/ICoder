from __future__ import annotations

from types import SimpleNamespace

import pytest

from icoder.llm.base import LlmError, StreamListener
from icoder.llm.deepseek import DeepSeekClient


class FakeCompletions:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def sdk_with(completions: FakeCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def completion(message, usage=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class RecordingStreamListener(StreamListener):
    def __init__(self) -> None:
        self.reasoning: list[str] = []
        self.content: list[str] = []

    def on_reasoning_delta(self, delta: str) -> None:
        self.reasoning.append(delta)

    def on_content_delta(self, delta: str) -> None:
        self.content.append(delta)


def test_normalizes_text_response_and_omits_empty_tools() -> None:
    completions = FakeCompletions(completion(SimpleNamespace(content="answer", tool_calls=None)))
    client = DeepSeekClient("key", sdk_client=sdk_with(completions))

    response = client.chat([{"role": "user", "content": "hello"}], [])

    assert response.content == "answer"
    assert not response.has_tool_calls
    assert completions.requests == [
        {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hello"}]}
    ]


def test_normalizes_multiple_tool_calls_and_reasoning() -> None:
    calls = [
        SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="read_file", arguments='{"path":"a.py"}'),
        ),
        SimpleNamespace(
            id="call-2",
            function=SimpleNamespace(name="list_dir", arguments="{}"),
        ),
    ]
    message = SimpleNamespace(content=None, reasoning_content="inspect", tool_calls=calls)
    completions = FakeCompletions(completion(message))
    client = DeepSeekClient("key", sdk_client=sdk_with(completions))
    tools = [{"type": "function", "function": {"name": "read_file"}}]

    response = client.chat([{"role": "user", "content": "inspect"}], tools)

    assert response.reasoning_content == "inspect"
    assert [call.name for call in response.tool_calls] == ["read_file", "list_dir"]
    assert response.tool_calls[0].as_message_dict()["id"] == "call-1"
    assert completions.requests[0]["tools"] == tools


def test_normalizes_provider_token_usage() -> None:
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17)
    completions = FakeCompletions(
        completion(SimpleNamespace(content="answer", tool_calls=None), usage)
    )
    client = DeepSeekClient("key", sdk_client=sdk_with(completions))

    response = client.chat([{"role": "user", "content": "hello"}])

    assert response.prompt_tokens == 12
    assert response.completion_tokens == 5
    assert response.total_tokens == 17


def test_rejects_empty_provider_response() -> None:
    completions = FakeCompletions(completion(SimpleNamespace(content=None, tool_calls=None)))
    client = DeepSeekClient("key", sdk_client=sdk_with(completions))

    with pytest.raises(LlmError, match="empty response"):
        client.chat([{"role": "user", "content": "hello"}])


def test_wraps_connection_failures() -> None:
    completions = FakeCompletions(error=TimeoutError("offline"))
    client = DeepSeekClient("key", sdk_client=sdk_with(completions))

    with pytest.raises(LlmError, match="connection failed"):
        client.chat([{"role": "user", "content": "hello"}])


def test_requires_non_empty_messages() -> None:
    client = DeepSeekClient("key", sdk_client=sdk_with(FakeCompletions()))

    with pytest.raises(ValueError, match="messages cannot be empty"):
        client.chat([])


def test_streams_reasoning_content_and_accumulates_tool_call_fragments() -> None:
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(
                reasoning_content="先分析",
                content=None,
                tool_calls=None,
            ))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(
                reasoning_content=None,
                content="准备读取",
                tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    function=SimpleNamespace(name="read_", arguments='{"path":"'),
                )],
            ))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(
                reasoning_content=None,
                content="文件",
                tool_calls=[SimpleNamespace(
                    index=0,
                    id=None,
                    function=SimpleNamespace(name="file", arguments='a.py"}'),
                )],
            ))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=5, total_tokens=13),
        ),
    ]
    completions = FakeCompletions(chunks)
    client = DeepSeekClient("key", sdk_client=sdk_with(completions))
    listener = RecordingStreamListener()

    response = client.chat_stream(
        [{"role": "user", "content": "inspect"}],
        listener=listener,
    )

    assert listener.reasoning == ["先分析"]
    assert listener.content == ["准备读取", "文件"]
    assert response.reasoning_content == "先分析"
    assert response.content == "准备读取文件"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == '{"path":"a.py"}'
    assert response.total_tokens == 13
    assert completions.requests[0]["stream"] is True
