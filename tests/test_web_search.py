from __future__ import annotations

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import web_search


def test_sports_commentary_followup_is_natural_search_candidate():
    query = "\u9510\u8bc4\u8377\u5170\u7684\u4e24\u4e2a\u8fb9\u950b"

    assert web_search.is_natural_candidate(query)


@pytest.mark.asyncio
async def test_sports_commentary_followup_calls_search(monkeypatch):
    query = "\u9510\u8bc4\u8377\u5170\u7684\u4e24\u4e2a\u8fb9\u950b"
    calls = []

    monkeypatch.setattr(
        web_search,
        "get_config",
        lambda: {
            "web_search": {
                "natural_query": True,
                "max_results": 3,
                "search_depth": "basic",
                "include_answer": True,
            }
        },
    )

    async def fake_search_web(search_query, **kwargs):
        calls.append(search_query)
        return {"answer": "match-analysis", "results": []}

    monkeypatch.setattr(web_search, "search_web", fake_search_web)

    result = await web_search.run_smart_search(query)

    assert calls
    assert "\u8db3\u7403" in calls[0]
    assert "match-analysis" in result
