from __future__ import annotations

import nonebot

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
