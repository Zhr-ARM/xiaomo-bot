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


def test_batch_current_message_uses_latest_same_user_followup_after_at():
    messages = [
        {
            "user_qq": "u1",
            "text": "小源，帮我看一下",
            "timestamp": 1,
            "explicit_trigger": True,
        },
        {
            "user_qq": "u1",
            "text": "报错是 connection refused",
            "timestamp": 2,
            "explicit_trigger": False,
            "dialogue_followup": True,
        },
    ]

    current = handlers._select_current_message(messages)

    assert current["text"] == "报错是 connection refused"


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


def test_batch_images_follow_latest_message_from_same_qq_only():
    own_image = {
        "url": "https://example.test/own.gif",
        "file": "own.gif",
        "user_qq": "u1",
    }
    messages = [
        {
            "user_qq": "u1",
            "text": "[非文本群消息]",
            "timestamp": 1,
            "dialogue_followup": True,
            "images": [own_image],
        },
        {
            "user_qq": "u2",
            "text": "这是我的图",
            "timestamp": 2,
            "images": [{"url": "https://example.test/other.jpg"}],
        },
        {
            "user_qq": "u1",
            "text": "这个表情也太真实了",
            "timestamp": 3,
            "dialogue_followup": True,
        },
    ]

    current = handlers._select_current_message(messages)

    assert current["text"] == "这个表情也太真实了"
    assert handlers._select_batch_images(messages, current) == [own_image]


def test_batch_context_renders_image_instead_of_non_text_placeholder():
    message = {
        "user_qq": "u1",
        "text": "[非文本群消息]",
        "images": [{"url": "https://example.test/a.gif"}],
    }

    context = handlers._format_batch_context(
        [message, {"user_qq": "u1", "text": "看这个"}],
        {"u1": "天照命"},
        message,
    )

    assert "[发送了1张图片]" in context
    assert "[非文本群消息]" not in context


def test_attached_image_forces_image_tool_plan_without_text_cue():
    frame = handlers.build_conversation_frame(
        current_msg={
            "user_qq": "u1",
            "text": "[非文本群消息]",
            "images": [{"url": "https://example.test/a.gif"}],
        },
        raw_text="[非文本群消息]",
        explicit_trigger=True,
    )

    assert frame.scene == "image_question"
    assert frame.tool_plan is not None
    assert frame.tool_plan.needs_image is True


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


def test_local_light_reaction_avoids_the_last_exact_reply():
    reply = handlers._choose_local_light_reaction(
        "哈哈哈哈",
        chooser=lambda replies: replies[0],
        avoid=["笑死"],
    )

    assert reply == "这句有点绷不住"


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


def test_dialogue_post_check_waits_for_speakers_newer_message(monkeypatch):
    from src.plugins.xiaomo import state

    state.group_recent_texts.clear()
    state.group_dialogue_sessions.clear()
    state.bot_reply_times.clear()
    state.bot_qq_id = "bot"
    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {
            "conversation_followup": {
                "post_check": {
                    "enabled": True,
                    "stale_seconds": 45,
                    "cancel_after_human_messages": 2,
                }
            }
        },
    )
    state.start_dialogue_session("g1", user_qq="u1", now=100.0, ttl_seconds=240)
    state.mark_dialogue_bot_reply("g1", user_qq="u1", now=102.0, ttl_seconds=240)
    state.record_recent_group_text(
        "g1",
        user_qq="u1",
        nickname="A",
        text="等等，我再补充一句",
        source_message_id="newer",
        now=110.0,
    )

    ok, reason = handlers._post_send_context_check(
        "g1",
        {
            "timestamp": 105.0,
            "source_message_id": "current",
            "user_qq": "u1",
            "dialogue_followup": True,
        },
        explicit_trigger=True,
        now=112.0,
    )

    assert ok is False
    assert reason == "speaker added another message"
    state.group_dialogue_sessions.clear()


def test_explicit_reply_yields_to_a_newer_turn_from_the_same_speaker():
    from src.plugins.xiaomo import state

    state.group_recent_texts.clear()
    state.group_recalled_messages.clear()
    state.record_recent_group_text(
        "g1",
        user_qq="u1",
        nickname="A",
        text="[image]",
        source_message_id="new-image",
        now=110.0,
    )

    ok, reason = handlers._post_send_context_check(
        "g1",
        {
            "timestamp": 100.0,
            "source_message_id": "old-request",
            "user_qq": "u1",
        },
        explicit_trigger=True,
        now=112.0,
    )

    assert ok is False
    assert reason == "speaker added a newer turn"
    state.group_recent_texts.clear()


def test_recalled_source_is_cancelled_before_send():
    from src.plugins.xiaomo import state

    state.group_recalled_messages.clear()
    state.record_group_recall("g1", "recalled-message", now=110.0)

    ok, reason = handlers._post_send_context_check(
        "g1",
        {
            "timestamp": 100.0,
            "source_message_id": "recalled-message",
            "user_qq": "u1",
        },
        explicit_trigger=True,
        now=112.0,
    )

    assert ok is False
    assert reason == "source message was recalled"
    state.group_recalled_messages.clear()


def test_short_visible_replies_keep_reasoning_budget():
    assert handlers._generation_token_budget(80) == 320
    assert handlers._generation_token_budget(900) == 1200
    assert handlers._generation_token_budget(80, solicited=True) == 768
    assert handlers._generation_token_budget(900, solicited=True) == 1600
    assert handlers._context_token_budget(8000, "casual_banter") == 3600
    assert handlers._context_token_budget(8000, "technical_help") == 8000


def test_turn_correction_and_generation_failure_replies_are_contextual():
    assert handlers._local_turn_correction_reply("谁问你了") == "确实接错话了，抱歉。"
    assert handlers._local_turn_correction_reply("继续说刚才那个") is None
    assert "再发一次" not in handlers._generation_failure_reply("这个怎么玩？")
    assert "不乱编" in handlers._generation_failure_reply("这个怎么玩？")


def test_repeat_wave_and_poke_replies_avoid_recent_wording():
    from src.plugins.xiaomo import state

    repeat = handlers._REPEAT_WAVE_REPLIES[0]
    assert handlers._choose_repeat_wave_reaction(
        avoid=[repeat], chooser=lambda values: values[0]
    ) != repeat

    poke = handlers._POKE_REPLIES[0]
    assert handlers._choose_poke_reply(
        [poke], chooser=lambda values: values[0]
    ) != poke

    state.poke_reply_group_last_time.clear()
    state.poke_reply_user_last_time.clear()
    state.bot_reply_times.clear()
    config = {
        "enabled": True,
        "group_cooldown_seconds": 360,
        "user_cooldown_seconds": 600,
        "after_bot_reply_seconds": 45,
    }
    allowed, reason = handlers._poke_reply_allowed(
        "g1", "u1", now=1000.0, cfg=config
    )
    assert allowed is True
    assert reason == "allowed"

    state.poke_reply_group_last_time["g1"] = 900.0
    allowed, reason = handlers._poke_reply_allowed(
        "g1", "u1", now=1000.0, cfg=config
    )
    assert allowed is False
    assert reason == "group cooldown"
    state.poke_reply_group_last_time.clear()


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


def test_plain_text_group_card_alias_only_counts_when_at_prefixed(monkeypatch):
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
        lambda: {
            "bot": {
                "nickname": "xiaoyuan",
                "at_aliases": ["CDUT Open Source"],
            }
        },
    )

    assert handlers._is_text_at_mention(Event("@CDUT Open Source hi")) is True
    assert handlers._is_called("CDUT Open Source has an event") is False


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

        context, meta, history = await memory.build_context(
            scene="group",
            user_qq="u2",
            group_id="g1",
            current_query="当前问题不该重复进历史",
            exclude_message_ids=[current_id],
        )

        joined_history = "\n".join(item["content"] for item in history)
        assert "[天照命 (QQ:u1)]: 上一条正常历史" in joined_history
        assert "当前问题不该重复进历史" not in joined_history
        assert "当前问题不该重复进历史" not in context
        assert meta["identity_qq"] == "u2"
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


def test_vector_search_filters_semantic_memory_by_speaker_qq(monkeypatch):
    captured = {}

    class FakeCollection:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {
                "ids": [["1"]],
                "documents": [["u1 的历史"]],
                "metadatas": [[{
                    "user_qq": "u1",
                    "group_id": "g1",
                    "created_at": 100.0,
                }]],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(vector_store, "_collection", FakeCollection())
    monkeypatch.setattr(vector_store, "_embed", lambda _texts: [[0.1, 0.2]])

    hits = vector_store._search_similar_sync(
        "历史",
        scene="group",
        group_id="g1",
        user_qq="u1",
        n_results=5,
        min_time=0,
        max_time=0,
    )

    assert hits[0]["user_qq"] == "u1"
    assert {"user_qq": {"$eq": "u1"}} in captured["where"]["$and"]
