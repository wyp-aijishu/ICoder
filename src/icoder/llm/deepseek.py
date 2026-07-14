"""DeepSeek client strategy."""

from __future__ import annotations

from typing import Any

from icoder.llm.openai_compatible import OpenAICompatibleClient

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_CONTEXT_TOKENS = 1_000_000


class DeepSeekClient(OpenAICompatibleClient):
    """OpenAI-compatible DeepSeek client."""

    @property
    def max_token(self) -> int:
        return MAX_CONTEXT_TOKENS

    @property
    def preserves_reasoning_content(self) -> bool:
        return True

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        sdk_client: Any | None = None,
    ) -> None:
        super().__init__(
            provider="deepseek",
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            sdk_client=sdk_client,
        )
