from __future__ import annotations

from icoder.tools.base import Tool, object_schema
from icoder.tools.registry import ToolRegistry, create_default_registry


def test_registry_definitions_are_openai_compatible_and_sorted() -> None:
    registry = ToolRegistry()
    registry.register(Tool("zeta", "z", object_schema({}), lambda _: "z"))
    registry.register(Tool("alpha", "a", object_schema({}), lambda _: "a"))

    definitions = registry.definitions()

    assert [item["function"]["name"] for item in definitions] == ["alpha", "zeta"]
    assert all(item["type"] == "function" for item in definitions)


def test_registry_rejects_duplicates() -> None:
    registry = ToolRegistry([Tool("sample", "", object_schema({}), lambda _: "ok")])

    try:
        registry.register(Tool("sample", "", object_schema({}), lambda _: "ok"))
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate registration should fail")


def test_registry_normalizes_invalid_json_and_unknown_tools() -> None:
    registry = ToolRegistry([Tool("sample", "", object_schema({}), lambda _: "ok")])

    malformed = registry.execute("sample", "{")
    unknown = registry.execute("missing", "{}")

    assert malformed.is_error and "invalid tool arguments JSON" in malformed.content
    assert unknown.is_error and "unknown tool" in unknown.content


def test_default_registry_contains_all_mvp_tools(tmp_path) -> None:
    registry = create_default_registry(tmp_path)

    assert len(registry) == 7
    for name in ("read_file", "write_file", "list_dir", "execute_command", "search_code"):
        assert name in registry
