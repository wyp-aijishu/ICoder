from __future__ import annotations

from unittest.mock import patch

import pytest

from icoder.llm.base import LlmConfigurationError
from icoder.llm.deepseek import DeepSeekClient
from icoder.llm.factory import LlmClientFactory
from icoder.llm.glm import GlmClient


def test_factory_creates_deepseek_from_environment() -> None:
    with patch("icoder.llm.openai_compatible.OpenAI") as openai:
        client = LlmClientFactory.create(
            "deepseek",
            environ={
                "DEEPSEEK_API_KEY": "secret",
                "DEEPSEEK_MODEL": "deepseek-custom",
                "DEEPSEEK_BASE_URL": "https://example.test/v1/",
            },
        )

    assert isinstance(client, DeepSeekClient)
    assert client.provider_name == "deepseek"
    assert client.model_name == "deepseek-custom"
    openai.assert_called_once_with(
        api_key="secret",
        base_url="https://example.test/v1",
        timeout=120.0,
        max_retries=2,
    )


def test_factory_uses_default_provider_alias_and_explicit_model() -> None:
    with patch("icoder.llm.openai_compatible.OpenAI"):
        client = LlmClientFactory.create(
            model="glm-custom",
            environ={"ICODER_PROVIDER": "zhipu", "GLM_API_KEY": "secret"},
        )

    assert isinstance(client, GlmClient)
    assert client.model_name == "glm-custom"


def test_factory_uses_provider_defaults() -> None:
    with patch("icoder.llm.openai_compatible.OpenAI"):
        client = LlmClientFactory.create("glm", environ={"GLM_API_KEY": "secret"})

    assert client.model_name == "glm-5.1"


@pytest.mark.parametrize("provider", ["", "unknown"])
def test_factory_rejects_invalid_provider(provider: str) -> None:
    with pytest.raises(LlmConfigurationError):
        LlmClientFactory.create(provider, environ={})


def test_factory_reports_missing_api_key() -> None:
    with pytest.raises(LlmConfigurationError, match="missing DEEPSEEK_API_KEY"):
        LlmClientFactory.create("deepseek", environ={})


def test_factory_rejects_explicit_empty_model() -> None:
    with pytest.raises(LlmConfigurationError, match="model cannot be empty"):
        LlmClientFactory.create(
            "glm", model=" ", environ={"GLM_API_KEY": "secret"}
        )
