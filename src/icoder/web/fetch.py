"""Fetch HTML pages and extract their main content as Markdown."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import trafilatura

EMPTY_CONTENT_MESSAGE = "未提取到正文。可能是 JS 渲染或防爬墙；本期范围内不再重试"
DEFAULT_USER_AGENT = "ICoder/0.1 (+https://github.com/)"


class WebFetchError(Exception):
    """Raised for invalid URLs and failed page requests."""


class WebFetcher:
    """Bounded HTTP fetcher backed by trafilatura content extraction."""

    def __init__(self, *, timeout: float = 20.0, max_bytes: int = 5_000_000) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes

    def fetch(self, url: str) -> str:
        normalized = self._validate_url(url)
        try:
            with httpx.stream(
                "GET",
                normalized,
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and "html" not in content_type and "text/plain" not in content_type:
                    raise WebFetchError(f"unsupported content type: {content_type.split(';', 1)[0]}")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise WebFetchError(f"page exceeds the {self._max_bytes}-byte limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                if not raw.strip():
                    return EMPTY_CONTENT_MESSAGE
                encoding = response.encoding or "utf-8"
                html = raw.decode(encoding, errors="replace")
        except httpx.TimeoutException as exc:
            raise WebFetchError("web fetch request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise WebFetchError(f"web fetch failed with HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise WebFetchError(f"web fetch request failed: {exc}") from exc

        markdown = trafilatura.extract(
            html,
            url=normalized,
            output_format="markdown",
            include_links=True,
            include_images=False,
            favor_precision=True,
        )
        return markdown.strip() if markdown and markdown.strip() else EMPTY_CONTENT_MESSAGE

    @staticmethod
    def _validate_url(url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise WebFetchError("url must be a non-empty string")
        normalized = url.strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise WebFetchError("url must be an absolute http or https URL")
        if parsed.username or parsed.password:
            raise WebFetchError("url must not contain credentials")
        return normalized