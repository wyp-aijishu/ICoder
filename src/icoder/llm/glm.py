"""Zhipu GLM client strategy."""

from __future__ import annotations

from typing import Any

from icoder.llm.openai_compatible import OpenAICompatibleClient

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
DEFAULT_MODEL = "glm-5.1"


class GlmClient(OpenAICompatibleClient):
    """OpenAI-compatible Zhipu GLM client."""

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
            provider="glm",
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            sdk_client=sdk_client,
        )
