from __future__ import annotations

from types import SimpleNamespace

from icoder.llm.deepseek import DeepSeekClient
from icoder.llm.glm import GlmClient


def fake_sdk():
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))


def test_provider_strategies_expose_identity() -> None:
    deepseek = DeepSeekClient("key", sdk_client=fake_sdk())
    glm = GlmClient("key", sdk_client=fake_sdk())

    assert (deepseek.provider_name, deepseek.model_name) == (
        "deepseek",
        "deepseek-v4-flash",
    )
    assert (glm.provider_name, glm.model_name) == ("glm", "glm-5.1")
