from __future__ import annotations

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import database, handlers, memory, vector_store, web_search


def test_batch_current_message_prefers_latest_explicit_trigger():
    messages = [
        {
            "user_qq": "u1",
            "text": "我先喊了一下",
            "timestamp": 1,
            "explicit_trigger": True,
            "search_text": "",
        },
        {
            "user_qq": "u2",
            "text": "搜索 Python 最新版本",
            "timestamp": 2,
            "explicit_trigger": True,
            "search_text": "搜索 Python 最新版本",
        },
    ]

    current = handlers._select_current_message(messages)

    assert current["user_qq"] == "u2"
    assert handlers._select_search_text(messages, current) == "搜索 Python 最新版本"


def test_batch_context_keeps_speakers_separate():
    messages = [
        {"user_qq": "u1", "text": "天照命发的 sb", "timestamp": 1},
        {"user_qq": "u2", "text": "这是另一个人说的", "timestamp": 2},
    ]
    current = messages[0]

    context = handlers._format_batch_context(
        messages,
        {"u1": "天照命", "u2": "其他人"},
        current,
    )
    prompt = handlers._format_current_user_message(
        user_display="天照命",
        user_qq="u1",
        raw_text="天照命发的 sb",
        batch_context=context,
    )

    assert "[天照命 (QQ:u1)] <= reply target" in prompt
    assert "[其他人 (QQ:u2)]" in prompt
    assert "[CURRENT_MESSAGE][天照命 (QQ:u1)]: 天照命发的 sb" in prompt


def test_plain_text_at_only_triggers_for_bot(monkeypatch):
    class Segment:
        type = "text"

        def __init__(self, text):
            self.data = {"text": text}

    class Event:
        def __init__(self, text):
            self.message = [Segment(text)]

    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {"bot": {"nickname": ["小源"]}},
    )

    assert handlers._is_text_at_mention(Event("@别人 查一下"), "123456") is False
    assert handlers._is_text_at_mention(Event("@小源查一下天气"), "123456") is True
    assert handlers._is_text_at_mention(Event("@123456 查一下"), "123456") is True


@pytest.mark.asyncio
async def test_search_result_reports_missing_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
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

    result = await web_search.run_smart_search_result("搜索 成都今天新闻")

    assert result.status == "not_configured"
    assert result.required is True
    assert "Tavily" in result.reason


@pytest.mark.asyncio
async def test_search_result_ok_keeps_backward_wrapper(monkeypatch):
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

    async def fake_search_web(query, **kwargs):
        return {"answer": "最新结果", "results": [{"title": "标题", "url": "https://example.com", "content": "内容"}]}

    monkeypatch.setattr(web_search, "search_web", fake_search_web)

    result = await web_search.run_smart_search_result("搜索 Python")
    legacy = await web_search.run_smart_search("搜索 Python")

    assert result.ok is True
    assert result.status == "ok"
    assert "最新结果" in result.context
    assert legacy == result.context


@pytest.mark.asyncio
async def test_context_excludes_current_message_and_loads_group_names(monkeypatch, tmp_path):
    await database.close_database()
    db_path = tmp_path / "xiaomo-test.db"
    monkeypatch.setattr(database, "get_config", lambda: {"database_path": str(db_path)})
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"memory": {"keep_recent_messages": 50}},
    )
    await database.init_database()

    try:
        async with await database.get_session() as session:
            user1 = await database.get_or_create_user(session, "u1")
            user1.nickname = "天照命"
            user2 = await database.get_or_create_user(session, "u2")
            user2.nickname = "其他人"
            await session.commit()

        await memory.store_memory("u1", "g1", "group", "user", "上一条正常历史")
        current_id = await memory.store_memory("u2", "g1", "group", "user", "当前问题不该重复进历史")

        context, _meta, history = await memory.build_context(
            scene="group",
            user_qq="u2",
            group_id="g1",
            current_query="当前问题不该重复进历史",
            exclude_message_ids=[current_id],
        )

        joined_history = "\n".join(item["content"] for item in history)
        assert "[天照命]: 上一条正常历史" in joined_history
        assert "当前问题不该重复进历史" not in joined_history
        assert "当前问题不该重复进历史" not in context
    finally:
        await database.close_database()


@pytest.mark.asyncio
async def test_vector_delete_messages_removes_ids(monkeypatch):
    deleted = []

    class FakeCollection:
        def delete(self, ids):
            deleted.extend(ids)

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())

    await vector_store.delete_messages([1, 2, 3])

    assert deleted == ["1", "2", "3"]
