from __future__ import annotations

import pytest

from icoder.mcp.bridge import create_mcp_tools, format_tool_result
from icoder.tools import ToolRegistry


class Item:
    def __init__(self, **values) -> None:
        self.__dict__.update(values)


class Caller:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def call_tool(self, server_name, tool_name, arguments):
        self.calls.append((server_name, tool_name, dict(arguments)))
        return self.result


def tool_definition(name: str = "take_snapshot") -> Item:
    return Item(
        name=name,
        description="Read a DOM snapshot",
        inputSchema={"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {}},
    )


def test_bridge_namespaces_tool_and_strips_schema_metadata() -> None:
    caller = Caller(Item(content=[Item(type="text", text="snapshot")], isError=False))
    tools = create_mcp_tools("chrome-devtools", [tool_definition()], caller)

    assert tools[0].name == "mcp__chrome_devtools__take_snapshot"
    assert "$schema" not in tools[0].parameters
    assert tools[0].handler({"verbose": True}) == "snapshot"
    assert caller.calls == [("chrome-devtools", "take_snapshot", {"verbose": True})]


def test_bridge_formats_structured_content_and_registry_error() -> None:
    result = Item(content=[Item(type="text", text="done")], structuredContent={"count": 1}, isError=False)
    assert format_tool_result(result) == 'done\n{"count": 1}'

    error_result = Item(content=[Item(type="text", text="denied")], isError=True)
    tool = create_mcp_tools("server", [tool_definition("run")], Caller(error_result))[0]
    executed = ToolRegistry([tool]).execute(tool.name, "{}")
    assert executed.is_error
    assert executed.content == "denied"


def test_bridge_rejects_binary_content() -> None:
    result = Item(content=[Item(type="image", data="base64")], isError=False)
    tool = create_mcp_tools("server", [tool_definition("screenshot")], Caller(result))[0]

    executed = ToolRegistry([tool]).execute(tool.name, "{}")

    assert executed.is_error
    assert "unsupported non-text content" in executed.content


def test_bridge_rejects_colliding_normalized_names() -> None:
    with pytest.raises(ValueError, match="collision"):
        create_mcp_tools("server", [tool_definition("one-two"), tool_definition("one_two")], Caller(None))