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


def test_ambient_context_block_is_available_for_proactive_join():
    block = handlers._format_ambient_context_block(
        "[天照命]: 有人来聊聊这个方案吗\n[其他人]: 感觉可以"
    )

    assert "[RECENT_GROUP_FLOW]" in block
    assert "有人来聊聊这个方案吗" in block


def test_join_reply_budget_respects_action_max_chars():
    budget = handlers._reply_budget_for_message(
        800,
        180,
        {"join_max_chars": 80},
    )

    assert budget == 80


def test_join_reply_budget_ignores_invalid_action_budget():
    budget = handlers._reply_budget_for_message(
        800,
        180,
        {"join_max_chars": "bad"},
    )

    assert budget == 180


def test_join_instruction_combines_ai_decision_with_reply_generation():
    instruction = handlers._format_join_instruction(
        {"action": "short_reply", "score": 58, "reason": "opinion", "max_chars": 160}
    )

    assert "output exactly [SILENT]" in instruction
    assert handlers._is_silent_join_reply("[SILENT]") is True
    assert handlers._is_silent_join_reply("这课确实教得有点脱节") is False


def test_local_light_reaction_handles_small_banter():
    reply = handlers._choose_local_light_reaction(
        "哈哈哈哈这也太离谱了",
        chooser=lambda replies: replies[0],
    )

    assert reply == "笑死"


def test_typing_delay_caps_proactive_react():
    delay = handlers._typing_delay_seconds(
        "这是一句轻反应",
        explicit_trigger=False,
        proactive=True,
        action="react",
        cfg={
            "enabled": True,
            "chars_per_second": 1,
            "jitter_seconds": 10,
            "min_seconds": 0.1,
            "max_seconds": 10,
            "proactive_max_seconds": 1.2,
        },
        jitter=10,
    )

    assert delay <= 0.8


def test_post_send_context_check_cancels_stale_proactive(monkeypatch):
    from src.plugins.xiaomo import state

    state.group_recent_texts.clear()
    state.bot_reply_times.clear()
    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {
            "proactive_join": {
                "post_check": {
                    "enabled": True,
                    "stale_seconds": 10,
                    "cancel_after_human_messages": 3,
                    "cancel_if_bot_spoke": True,
                }
            }
        },
    )

    ok, reason = handlers._post_send_context_check(
        "g1",
        {"timestamp": 100.0, "join_instruction": "yes"},
        explicit_trigger=False,
        now=120.0,
    )

    assert ok is False
    assert reason == "stale"


def test_post_send_context_check_cancels_when_humans_continue(monkeypatch):
    from src.plugins.xiaomo import state

    state.group_recent_texts.clear()
    state.bot_reply_times.clear()
    state.bot_qq_id = "bot"
    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {
            "proactive_join": {
                "post_check": {
                    "enabled": True,
                    "stale_seconds": 60,
                    "cancel_after_human_messages": 2,
                    "cancel_if_bot_spoke": True,
                }
            }
        },
    )
    state.record_recent_group_text("g1", user_qq="u1", nickname="A", text="新话题1", now=110.0)
    state.record_recent_group_text("g1", user_qq="u2", nickname="B", text="新话题2", now=111.0)

    ok, reason = handlers._post_send_context_check(
        "g1",
        {"timestamp": 100.0, "join_instruction": "yes"},
        explicit_trigger=False,
        now=112.0,
    )

    assert ok is False
    assert reason == "humans continued"


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
