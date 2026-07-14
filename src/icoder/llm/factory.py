"""Factory for configured LLM client strategies."""

from __future__ import annotations

import os
from collections.abc import Mapping

from dotenv import load_dotenv

from icoder.llm.base import LlmClient, LlmConfigurationError
from icoder.llm.deepseek import DEFAULT_BASE_URL as DEEPSEEK_BASE_URL
from icoder.llm.deepseek import DEFAULT_MODEL as DEEPSEEK_MODEL
from icoder.llm.deepseek import DeepSeekClient
from icoder.llm.glm import DEFAULT_BASE_URL as GLM_BASE_URL
from icoder.llm.glm import DEFAULT_MODEL as GLM_MODEL
from icoder.llm.glm import GlmClient

_PROVIDER_ALIASES = {
    "deepseek": "deepseek",
    "deep-seek": "deepseek",
    "glm": "glm",
    "zhipu": "glm",
    "bigmodel": "glm",
}


class LlmClientFactory:
    """Create provider strategies from explicit values and environment config."""

    @classmethod
    def create(
        cls,
        provider: str | None = None,
        *,
        model: str | None = None,
        environ: Mapping[str, str] | None = None,
        load_env_file: bool = True,
        timeout: float = 120.0,
    ) -> LlmClient:
        if load_env_file and environ is None:
            load_dotenv(override=False)
        config = os.environ if environ is None else environ
        selected = provider or config.get("ICODER_PROVIDER", "deepseek")
        normalized = cls.normalize_provider(selected)

        if normalized == "deepseek":
            return DeepSeekClient(
                cls._api_key(config, "DEEPSEEK_API_KEY", normalized),
                model=cls._model(model, config.get("DEEPSEEK_MODEL"), DEEPSEEK_MODEL),
                base_url=cls._configured(config.get("DEEPSEEK_BASE_URL"), DEEPSEEK_BASE_URL),
                timeout=timeout,
            )
        if normalized == "glm":
            return GlmClient(
                cls._api_key(config, "GLM_API_KEY", normalized),
                model=cls._model(model, config.get("GLM_MODEL"), GLM_MODEL),
                base_url=cls._configured(config.get("GLM_BASE_URL"), GLM_BASE_URL),
                timeout=timeout,
            )
        raise AssertionError(f"unhandled provider: {normalized}")

    @staticmethod
    def normalize_provider(provider: object) -> str:
        if not isinstance(provider, str) or not provider.strip():
            raise LlmConfigurationError("provider cannot be empty")
        raw = provider.strip().lower()
        normalized = _PROVIDER_ALIASES.get(raw)
        if normalized is None:
            choices = ", ".join(sorted({*(_PROVIDER_ALIASES.values())}))
            raise LlmConfigurationError(
                f"unknown provider '{provider}'; supported providers: {choices}"
            )
        return normalized

    @staticmethod
    def _api_key(config: Mapping[str, str], key: str, provider: str) -> str:
        value = config.get(key, "").strip()
        if not value:
            raise LlmConfigurationError(
                f"missing {key}; configure an API key before using provider '{provider}'"
            )
        return value

    @staticmethod
    def _model(explicit: str | None, configured: str | None, fallback: str) -> str:
        if explicit is not None and not explicit.strip():
            raise LlmConfigurationError("model cannot be empty")
        return explicit.strip() if explicit is not None else LlmClientFactory._configured(configured, fallback)

    @staticmethod
    def _configured(value: str | None, fallback: str) -> str:
        return value.strip() if value is not None and value.strip() else fallback
