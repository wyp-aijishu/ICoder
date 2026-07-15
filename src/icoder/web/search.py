"""Web search strategies and environment-backed factory."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

DEFAULT_GLM_SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"
DEFAULT_SERPAPI_URL = "https://serpapi.com/search.json"


class WebSearchError(Exception):
    """Raised when search configuration or a provider request is invalid."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Provider-independent search result."""

    title: str
    url: str
    snippet: str = ""
    source: str = ""


class SearchEngine(ABC):
    """Strategy interface implemented by web search providers."""

    @abstractmethod
    def search(self, query: str, *, count: int) -> list[SearchResult]:
        """Search the web and return normalized results."""


class GlmSearchEngine(SearchEngine):
    """Zhipu web-search API strategy."""

    def __init__(self, api_key: str, *, endpoint: str, timeout: float) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout = timeout

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        payload = {
            "search_engine": "search_std",
            "search_query": query,
            "count": count,
            "content_size": "medium",
        }
        data = _request_json(
            "POST",
            self._endpoint,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
        )
        items = data.get("search_result", [])
        if not isinstance(items, list):
            raise WebSearchError("GLM search returned an invalid response")
        return [
            SearchResult(
                title=_text(item.get("title")) or _text(item.get("link")),
                url=_text(item.get("link")),
                snippet=_text(item.get("content")),
                source=_text(item.get("media")),
            )
            for item in items
            if isinstance(item, dict) and _text(item.get("link"))
        ][:count]


class SerpApiSearchEngine(SearchEngine):
    """Google results through SerpAPI."""

    def __init__(self, api_key: str, *, endpoint: str, timeout: float) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._timeout = timeout

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        data = _request_json(
            "GET",
            self._endpoint,
            timeout=self._timeout,
            params={"engine": "google", "q": query, "num": count, "api_key": self._api_key},
        )
        if data.get("error"):
            raise WebSearchError(f"SerpAPI search failed: {_text(data['error'])}")
        items = data.get("organic_results", [])
        if not isinstance(items, list):
            raise WebSearchError("SerpAPI returned an invalid response")
        return [
            SearchResult(
                title=_text(item.get("title")) or _text(item.get("link")),
                url=_text(item.get("link")),
                snippet=_text(item.get("snippet")),
                source=_text(item.get("source")),
            )
            for item in items
            if isinstance(item, dict) and _text(item.get("link"))
        ][:count]


class SearchEngineFactory:
    """Create a search strategy from explicit values or environment config."""

    @classmethod
    def create(
        cls,
        provider: str | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        load_env_file: bool = True,
        timeout: float = 20.0,
    ) -> SearchEngine:
        if load_env_file and environ is None:
            load_dotenv(override=False)
        config = os.environ if environ is None else environ
        selected = (provider or config.get("WEB_SEARCH_PROVIDER", "glm")).strip().lower()
        if selected in {"glm", "zhipu", "bigmodel"}:
            return GlmSearchEngine(
                cls._api_key(config, "GLM_API_KEY", "glm"),
                endpoint=_configured(config.get("GLM_WEB_SEARCH_URL"), DEFAULT_GLM_SEARCH_URL),
                timeout=timeout,
            )
        if selected in {"serpapi", "serp"}:
            return SerpApiSearchEngine(
                cls._api_key(config, "SERPAPI_API_KEY", "serpapi"),
                endpoint=_configured(config.get("SERPAPI_URL"), DEFAULT_SERPAPI_URL),
                timeout=timeout,
            )
        raise WebSearchError(f"unknown web search provider '{selected}'; supported providers: glm, serpapi")

    @staticmethod
    def _api_key(config: Mapping[str, str], name: str, provider: str) -> str:
        value = config.get(name, "").strip()
        if not value:
            raise WebSearchError(f"missing {name}; configure it before using provider '{provider}'")
        return value


def _request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = httpx.request(method, url, **kwargs)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise WebSearchError("web search request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise WebSearchError(f"web search request failed with HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise WebSearchError(f"web search request failed: {exc}") from exc
    if not isinstance(data, dict):
        raise WebSearchError("web search returned an invalid JSON response")
    return data


def _configured(value: str | None, fallback: str) -> str:
    return value.strip() if value is not None and value.strip() else fallback


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""