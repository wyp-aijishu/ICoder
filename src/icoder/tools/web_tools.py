"""Agent tool adapters for web search and page fetching."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from icoder.tools.base import Tool, ToolError, object_schema
from icoder.web.fetch import WebFetchError, WebFetcher
from icoder.web.search import SearchEngine, SearchEngineFactory, WebSearchError

DEFAULT_RESULT_COUNT = 5
MAX_RESULT_COUNT = 10
MAX_OUTPUT_CHARS = 30_000


def create_web_tools(
    *,
    search_engine: SearchEngine | None = None,
    fetcher: WebFetcher | None = None,
) -> tuple[Tool, ...]:
    """Create lazy-configured web tools suitable for the default registry."""
    page_fetcher = fetcher or WebFetcher()

    def web_search(arguments: Mapping[str, Any]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty string")
        count = _result_count(arguments.get("count", DEFAULT_RESULT_COUNT))
        provider = arguments.get("provider")
        if provider is not None and (not isinstance(provider, str) or not provider.strip()):
            raise ToolError("provider must be glm or serpapi")
        try:
            engine = search_engine or SearchEngineFactory.create(provider)
            results = engine.search(query.strip(), count=count)
        except WebSearchError as exc:
            raise ToolError(str(exc)) from exc
        if not results:
            return "no web search results found"
        rendered = []
        for index, result in enumerate(results, start=1):
            source = f" ({result.source})" if result.source else ""
            block = f"{index}. [{result.title}]({result.url}){source}"
            if result.snippet:
                block += f"\n   {result.snippet}"
            rendered.append(block)
        return _truncate("\n\n".join(rendered))

    def web_fetch(arguments: Mapping[str, Any]) -> str:
        url = arguments.get("url")
        try:
            return _truncate(page_fetcher.fetch(url))  # type: ignore[arg-type]
        except WebFetchError as exc:
            raise ToolError(str(exc)) from exc

    return (
        Tool(
            name="web_search",
            description="Search the public web and return titles, URLs, and snippets.",
            parameters=object_schema(
                {
                    "query": {"type": "string", "description": "Search query."},
                    "count": {"type": "integer", "minimum": 1, "maximum": MAX_RESULT_COUNT},
                    "provider": {"type": "string", "enum": ["glm", "serpapi"]},
                },
                ("query",),
            ),
            handler=web_search,
        ),
        Tool(
            name="web_fetch",
            description="Fetch an HTTP(S) page and return its main content as Markdown.",
            parameters=object_schema(
                {"url": {"type": "string", "description": "Absolute HTTP(S) page URL."}},
                ("url",),
            ),
            handler=web_fetch,
        ),
    )


def _result_count(value: object) -> int:
    if isinstance(value, bool):
        raise ToolError("count must be an integer")
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ToolError("count must be an integer") from exc
    if not 1 <= count <= MAX_RESULT_COUNT:
        raise ToolError(f"count must be between 1 and {MAX_RESULT_COUNT}")
    return count


def _truncate(content: str) -> str:
    if len(content) <= MAX_OUTPUT_CHARS:
        return content
    return content[:MAX_OUTPUT_CHARS] + "\n...[web output truncated]"