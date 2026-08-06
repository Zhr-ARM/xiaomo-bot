from __future__ import annotations

import nonebot
import pytest
from sqlalchemy import func, select

nonebot.init()

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message as OneBotMessage
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11 import PrivateMessageEvent

from src.plugins.xiaomo import database, delivery, handlers, intelligence, memory, state


def test_ordinary_outdoor_sentence_does_not_trigger_weather():
    assert handlers._is_weather_query("我在外面吃饭") is None


def _group_event(
    *,
    message_id: int,
    user_id: int,
    text: str = "",
    at_bot: bool = False,
    card: str = "群名片",
) -> GroupMessageEvent:
    segments = []
    if text:
        segments.append(MessageSegment.text(text))
    if at_bot:
        segments.append(MessageSegment.at(3115709797))
    return GroupMessageEvent(
        time=1,
        self_id=3115709797,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=message_id,
        group_id=1070638552,
        user_id=user_id,
        anonymous=None,
        message=OneBotMessage(segments),
        raw_message=str(OneBotMessage(segments)),
        font=0,
        sender={
            "user_id": user_id,
            "nickname": "QQ昵称",
            "card": card,
            "role": "member",
        },
    )


class _FakeBot:
    self_id = "3115709797"

    async def call_api(self, *_args, **_kwargs):
        raise AssertionError("get_msg should not be needed in this scenario")


class _CaptureWindow:
    def __init__(self):
        self.items = []

    def enqueue(self, key, message, **kwargs):
        self.items.append((key, message, kwargs))


@pytest.mark.asyncio
async def test_private_message_is_ignored_without_persistence(monkeypatch):
    event = PrivateMessageEvent(
        time=1,
        self_id=3115709797,
        post_type="message",
        message_type="private",
        sub_type="friend",
        message_id=11,
        user_id=42,
        message=OneBotMessage("私聊测试"),
        raw_message="私聊测试",
        font=0,
        sender={"user_id": 42, "nickname": "私聊成员"},
    )

    async def initialized():
        return None

    async def fail_store(**_kwargs):
        raise AssertionError("private messages must not be stored")

    monkeypatch.setattr(handlers, "_ensure_init", initialized)
    monkeypatch.setattr(handlers, "get_bot", lambda: _FakeBot())
    monkeypatch.setattr(handlers, "store_inbound_memory", fail_store)

    await handlers._on_message(event)


@pytest.mark.asyncio
async def test_split_text_then_standalone_at_is_one_contextual_turn(
    monkeypatch, tmp_path
):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "events.db")},
    )
    await database.init_database()

    capture = _CaptureWindow()
    monkeypatch.setattr(handlers, "get_bot", lambda: _FakeBot())
    monkeypatch.setattr(handlers, "get_silent_window", lambda: capture)
    monkeypatch.setattr(handlers, "_is_allowed_group", lambda _group_id: True)
    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {
            "bot": {"nickname": "小源"},
            "silent_window": {"explicit_group_seconds": 0.01},
            "proactive_join": {"enabled": False, "recent_context_messages": 8},
            "reactions": {"triggers": {}},
            "poke_everyone_cooldown_minutes": 0,
        },
    )

    async def initialized():
        return None

    monkeypatch.setattr(handlers, "_ensure_init", initialized)
    from src.plugins.xiaomo import runtime_state

    monkeypatch.setattr(runtime_state, "schedule_persist", lambda *_args, **_kwargs: None)
    state.group_recent_texts.clear()
    state.group_dialogue_sessions.clear()
    state.group_message_times.clear()
    state.bot_reply_times.clear()
    state.bot_qq_id = "3115709797"

    try:
        first = _group_event(
            message_id=501,
            user_id=26,
            text="我怎么没在6b504看到猫娘",
            card="26智能工人新生",
        )
        second = _group_event(
            message_id=502,
            user_id=26,
            at_bot=True,
            card="26智能工人新生",
        )

        await handlers._on_message(first)
        assert capture.items == []
        await handlers._on_message(second)

        assert len(capture.items) == 1
        queued = capture.items[0][1]
        assert "我怎么没在6b504看到猫娘" in queued["text"]
        assert "承接该成员上一条消息" in queued["text"]
        assert queued["explicit_trigger"] is True

        async with await database.get_session() as session:
            count = await session.scalar(select(func.count(database.Message.id)))
            profile = await database.get_user_profile_summary(session, "26")
        assert count == 2
        assert profile["nickname"] == "26智能工人新生"

        # A replay of the same OneBot event must not enqueue or store again.
        await handlers._on_message(second)
        assert len(capture.items) == 1
        async with await database.get_session() as session:
            count = await session.scalar(select(func.count(database.Message.id)))
        assert count == 2
    finally:
        state.group_dialogue_sessions.clear()
        await database.close_database()


@pytest.mark.asyncio
async def test_initial_at_opens_fast_no_mention_followup(monkeypatch, tmp_path):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "dialogue-events.db")},
    )
    await database.init_database()

    capture = _CaptureWindow()
    monkeypatch.setattr(handlers, "get_bot", lambda: _FakeBot())
    monkeypatch.setattr(handlers, "get_silent_window", lambda: capture)
    monkeypatch.setattr(handlers, "_is_allowed_group", lambda _group_id: True)
    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {
            "bot": {"nickname": "小源", "qq_id": "3115709797"},
            "silent_window": {"explicit_group_seconds": 0.01},
            "conversation_followup": {
                "enabled": True,
                "timeout_seconds": 240,
                "window_seconds": 0.02,
                "max_intervening_human_messages": 2,
            },
            "proactive_join": {"enabled": False, "recent_context_messages": 8},
            "reactions": {"triggers": {}},
            "poke_everyone_cooldown_minutes": 0,
        },
    )

    async def initialized():
        return None

    monkeypatch.setattr(handlers, "_ensure_init", initialized)
    from src.plugins.xiaomo import runtime_state

    monkeypatch.setattr(runtime_state, "schedule_persist", lambda *_args, **_kwargs: None)
    state.group_recent_texts.clear()
    state.group_dialogue_sessions.clear()
    state.group_message_times.clear()
    state.bot_reply_times.clear()
    state.bot_qq_id = "3115709797"

    try:
        await handlers._on_message(
            _group_event(
                message_id=601,
                user_id=26,
                text="帮我看看这个报错",
                at_bot=True,
            )
        )
        assert len(capture.items) == 1
        assert capture.items[0][1]["explicit_trigger"] is True

        state.mark_dialogue_bot_reply(
            "1070638552",
            user_qq="26",
            text="把完整报错贴一下",
            source_message_id="bot-601",
        )
        capture.items.clear()

        await handlers._on_message(
            _group_event(
                message_id=602,
                user_id=26,
                text="就是 connection refused",
            )
        )

        assert len(capture.items) == 1
        queued = capture.items[0][1]
        assert queued["explicit_trigger"] is False
        assert queued["dialogue_followup"] is True
        assert "[DIALOGUE_CONTINUATION_CHECK]" in queued["dialogue_instruction"]
        assert capture.items[0][2]["wait_seconds"] == 0.02
    finally:
        state.group_dialogue_sessions.clear()
        await database.close_database()


def test_standalone_at_never_steals_another_members_previous_text():
    state.group_recent_texts.clear()
    state.record_recent_group_text(
        "g1",
        user_qq="u1",
        nickname="甲",
        text="这是甲的问题",
        source_message_id="1",
        now=100.0,
    )

    text, carried = handlers._resolve_message_text(
        group_id="g1",
        user_qq="u2",
        source_message_id="2",
        text="",
        at_text="",
        should_respond=True,
        now=110.0,
    )

    assert carried is False
    assert text == "[有成员@了小源]"


def test_other_mentions_keep_their_display_name():
    class Segment:
        def __init__(self, kind, data):
            self.type = kind
            self.data = data

    event = type(
        "Event",
        (),
        {
            "message": [
                Segment("text", {"text": "这是谁发的"}),
                Segment("at", {"qq": "42", "name": "天照命"}),
                Segment("at", {"qq": "bot", "name": "小源"}),
            ]
        },
    )()

    assert handlers._extract_at_text(event, "bot") == "这是谁发的 @天照命"


def test_ordinary_how_question_does_not_plan_a_web_search():
    plan = intelligence.plan_tools(
        "我怎么没在6b504看到猫娘",
        explicit_trigger=True,
    )
    live_plan = intelligence.plan_tools(
        "Python现在最新版本是什么",
        explicit_trigger=True,
    )
    daily_chat_plan = intelligence.plan_tools(
        "我今天真的好累啊",
        explicit_trigger=True,
    )

    assert plan.needs_search is False
    assert live_plan.needs_search is True
    assert daily_chat_plan.needs_search is False


@pytest.mark.asyncio
async def test_failed_delivery_does_not_create_assistant_memory(monkeypatch):
    calls = []

    class FailingBot:
        async def send_group_msg(self, **_kwargs):
            calls.append("send")
            raise RuntimeError("network down")

    async def fake_store_memory(**_kwargs):
        calls.append("memory")

    monkeypatch.setattr(memory, "store_memory", fake_store_memory)

    with pytest.raises(RuntimeError, match="network down"):
        await delivery.send_group_text(FailingBot(), "1", "hello")

    assert calls == ["send"]


@pytest.mark.asyncio
async def test_delivery_timeout_releases_pipeline_without_memory_or_retry(monkeypatch):
    calls = []

    class HangingBot:
        async def send_group_msg(self, **_kwargs):
            calls.append("send")
            await __import__("asyncio").sleep(10)

    async def fake_store_memory(**_kwargs):
        calls.append("memory")

    monkeypatch.setattr(memory, "store_memory", fake_store_memory)
    monkeypatch.setattr(
        delivery,
        "get_config",
        lambda: {"delivery": {"send_timeout_seconds": 0.01}},
    )

    with pytest.raises(delivery.DeliveryTimeoutError):
        await delivery.send_group_text(HangingBot(), "1", "hello")

    assert calls == ["send"]


@pytest.mark.asyncio
async def test_reply_context_uses_local_source_mapping_and_group_card(
    monkeypatch, tmp_path
):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "reply.db")},
    )
    await database.init_database()

    class NoApiBot:
        async def call_api(self, *_args, **_kwargs):
            raise AssertionError("local reply mapping should be enough")

    try:
        await memory.store_inbound_memory(
            group_id="g1",
            source_message_id="9001",
            user_qq="1458741024",
            nickname="天照命",
            content="sb",
        )

        context, quoted_qq = await handlers._resolve_reply_context(
            NoApiBot(),
            group_id="g1",
            reply_to_message_id="9001",
        )

        assert quoted_qq == "1458741024"
        assert "天照命" in context
        assert "sb" in context
    finally:
        await database.close_database()


@pytest.mark.asyncio
async def test_reply_context_recognizes_locally_stored_bot_message(monkeypatch, tmp_path):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "bot-reply.db")},
    )
    await database.init_database()

    class NoApiBot:
        async def call_api(self, *_args, **_kwargs):
            raise AssertionError("local assistant mapping should be enough")

    state.bot_qq_id = "3115709797"
    try:
        stored_id = await memory.store_memory(
            None,
            "g1",
            "group",
            "assistant",
            "把完整报错贴一下",
        )
        async with await database.get_session() as session:
            await database.link_source_message_id(
                session,
                group_id="g1",
                source_message_id="bot-9002",
                message_id=stored_id,
            )
            await session.commit()

        context, quoted_qq = await handlers._resolve_reply_context(
            NoApiBot(),
            group_id="g1",
            reply_to_message_id="bot-9002",
        )

        assert quoted_qq == "3115709797"
        assert "小源" in context
        assert "把完整报错贴一下" in context
    finally:
        await database.close_database()


@pytest.mark.asyncio
async def test_replayed_source_id_cannot_change_its_speaker_qq(monkeypatch, tmp_path):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "identity-conflict.db")},
    )
    await database.init_database()

    try:
        async with await database.get_session() as session:
            await database.save_inbound_group_message(
                session,
                group_id="g1",
                source_message_id="same-message",
                user_qq="u1",
                nickname="甲",
                content="第一条",
            )
            await session.commit()

        async with await database.get_session() as session:
            with pytest.raises(database.InboundIdentityConflict):
                await database.save_inbound_group_message(
                    session,
                    group_id="g1",
                    source_message_id="same-message",
                    user_qq="u2",
                    nickname="乙",
                    content="伪造重放",
                )
    finally:
        await database.close_database()


@pytest.mark.asyncio
async def test_memory_compression_deletes_only_the_summarized_batch(
    monkeypatch, tmp_path
):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "compression.db")},
    )
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"memory": {"keep_recent_messages": 20}},
    )
    await database.init_database()

    summarized_inputs = []

    class SummaryLLM:
        async def generate_summary(self, text):
            summarized_inputs.append(text)
            return "batch summary"

    from src.plugins.xiaomo import llm

    monkeypatch.setattr(llm, "get_llm", lambda: SummaryLLM())

    try:
        async with await database.get_session() as session:
            for index in range(160):
                await database.save_message(
                    session,
                    user_qq="u1",
                    group_id="g1",
                    scene="group",
                    role="user",
                    content=f"message-{index}-" + ("x" * 80),
                )
            await session.commit()

        await memory.compress_old_memories("group", "u1", "g1", threshold=1)

        async with await database.get_session() as session:
            remaining = list(
                (
                    await session.execute(
                        select(database.Message).order_by(database.Message.id)
                    )
                )
                .scalars()
                .all()
            )
            summaries = list(
                (await session.execute(select(database.ContextSummary))).scalars().all()
            )

        assert len(remaining) == 60
        assert remaining[0].id == 101
        assert len(summaries) == 1
        assert summaries[0].start_message_id == 1
        assert summaries[0].end_message_id == 100
        assert summaries[0].user_qq is None
        assert "[QQu1 (QQ:u1)]" in summarized_inputs[0]
    finally:
        await database.close_database()


@pytest.mark.asyncio
async def test_runtime_social_state_survives_restart(monkeypatch, tmp_path):
    from src.plugins.xiaomo import runtime_state

    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "runtime.db")},
    )
    await database.init_database()

    state.group_last_active.clear()
    state.proactive_join_feedback.clear()
    state.group_dialogue_sessions.clear()
    state.group_last_active["g1"] = 123.0
    state.proactive_join_feedback["g1"] = {"score": 0.16}
    state.group_dialogue_sessions["g1"] = {
        "user_qq": "u1",
        "expires_at": 9999999999.0,
    }

    try:
        await runtime_state.persist_now()
        state.group_last_active.clear()
        state.proactive_join_feedback.clear()
        state.group_dialogue_sessions.clear()

        await runtime_state.restore()

        assert state.group_last_active == {"g1": 123.0}
        assert state.proactive_join_feedback["g1"]["score"] == 0.16
        assert state.group_dialogue_sessions["g1"]["user_qq"] == "u1"
    finally:
        state.group_last_active.clear()
        state.proactive_join_feedback.clear()
        state.group_dialogue_sessions.clear()
        await database.close_database()
