from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import llm


def test_llm_client_uses_configured_timeout_and_retries(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(
        llm,
        "get_config",
        lambda: {
            "llm": {
                "api_key": "test-key",
                "api_base": "https://example.test/v1",
                "timeout_seconds": 18,
                "max_retries": 0,
            }
        },
    )

    client = llm.LLMClient()

    assert client.timeout_seconds == 18
    assert client.max_retries == 0
    assert captured["timeout"] == 18
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_llm_discards_a_visibly_truncated_short_completion():
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(content="three chars"),
                    )
                ]
            )

    client = object.__new__(llm.LLMClient)
    client.model = "test-model"
    client.max_tokens = 4096
    client.temperature = 0.8
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    reply = await client.chat(
        context="",
        user_message="test",
        max_tokens=320,
        system_prompt="test system",
    )

    assert reply == ""
