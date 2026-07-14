from io import StringIO

from icoder.cli.streaming import CliStreamRenderer
from icoder.llm.base import ToolCall


def test_cli_stream_renderer_flushes_reasoning_content_and_tool_progress() -> None:
    output = StringIO()
    renderer = CliStreamRenderer(output)
    call = ToolCall("call-1", "read_file", '{"path":"a.py"}')

    renderer.reset_turn()
    renderer.on_reasoning_delta("分析")
    renderer.on_tool_start(call)
    renderer.on_tool_end(call, "line one\nline two", is_error=False)
    renderer.on_llm_start()
    renderer.on_content_delta("完成")

    rendered = output.getvalue()
    assert "🧠 思考: 分析" in rendered
    assert '🔧 调用工具 read_file: {"path":"a.py"}' in rendered
    assert "✅ read_file 完成: line one line two" in rendered
    assert "💬 回复: 完成" in rendered
    assert renderer.streamed_content is True