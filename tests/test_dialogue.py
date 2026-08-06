from __future__ import annotations

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import dialogue, state


@pytest.fixture(autouse=True)
def clear_dialogue_state():
    state.group_dialogue_sessions.clear()
    state.group_recent_texts.clear()
    state.bot_reply_times.clear()
    yield
    state.group_dialogue_sessions.clear()
    state.group_recent_texts.clear()
    state.bot_reply_times.clear()


def _open_replied_dialogue(*, now: float = 100.0) -> None:
    state.start_dialogue_session(
        "g1",
        user_qq="u1",
        source_message_id="m1",
        now=now,
        ttl_seconds=240,
    )
    state.mark_dialogue_bot_reply(
        "g1",
        user_qq="u1",
        text="你先把报错贴出来，我看看",
        source_message_id="bot-1",
        now=now + 2,
        ttl_seconds=240,
    )


def test_same_member_can_continue_without_mentioning_bot():
    _open_replied_dialogue()

    decision = dialogue.evaluate_continuation(
        group_id="g1",
        user_qq="u1",
        text="报错是 connection refused",
        bot_qq="bot",
        now=110.0,
    )

    assert decision.candidate is True
    assert decision.reason == "recent-same-owner-turn"
    instruction = dialogue.build_continuation_instruction(decision)
    assert "[SILENT]" in instruction
    assert "你先把报错贴出来" in instruction


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"user_qq": "u2"}, "different-dialogue-owner"),
        ({"mentioned_qqs": ["u2"]}, "mentions-another-member"),
        (
            {"reply_to_message_id": "human-1", "quoted_user_qq": "u2"},
            "replies-to-another-message",
        ),
        ({"text": "大家今晚有人打游戏吗"}, "addresses-the-group"),
    ],
)
def test_followup_guard_does_not_steal_messages_for_people_or_group(kwargs, reason):
    _open_replied_dialogue()
    values = {
        "group_id": "g1",
        "user_qq": "u1",
        "text": "那这个怎么处理",
        "bot_qq": "bot",
        "now": 110.0,
    }
    values.update(kwargs)

    decision = dialogue.evaluate_continuation(**values)

    assert decision.candidate is False
    assert decision.reason == reason


def test_direct_reply_to_bot_can_start_a_dialogue_without_an_at():
    decision = dialogue.evaluate_continuation(
        group_id="g1",
        user_qq="u2",
        text="对，就是这个意思",
        bot_qq="bot",
        reply_to_message_id="bot-2",
        quoted_user_qq="bot",
        now=110.0,
    )

    assert decision.candidate is True
    assert decision.direct_reply is True
    assert decision.reason == "direct-reply-to-bot"


def test_multiple_intervening_members_end_implicit_continuation():
    _open_replied_dialogue()
    for index, user in enumerate(("u2", "u3", "u4"), start=1):
        state.record_recent_group_text(
            "g1",
            user_qq=user,
            nickname=user,
            text=f"群友消息 {index}",
            source_message_id=f"h{index}",
            now=103.0 + index,
        )

    decision = dialogue.evaluate_continuation(
        group_id="g1",
        user_qq="u1",
        text="那接着说",
        bot_qq="bot",
        now=110.0,
        config={"max_intervening_human_messages": 2},
    )

    assert decision.candidate is False
    assert decision.reason == "group-conversation-moved-on"


def test_pending_explicit_turn_accepts_the_senders_next_message_in_same_window():
    state.start_dialogue_session(
        "g1",
        user_qq="u1",
        source_message_id="m1",
        now=100.0,
        ttl_seconds=240,
    )

    decision = dialogue.evaluate_continuation(
        group_id="g1",
        user_qq="u1",
        text="我再补一句",
        bot_qq="bot",
        now=101.0,
        config={"pending_seconds": 15},
    )

    assert decision.candidate is True
    assert decision.reason == "same-owner-after-explicit"


def test_older_reply_cannot_overwrite_newer_dialogue_owner():
    state.start_dialogue_session("g1", user_qq="u1", now=100.0, ttl_seconds=240)
    state.start_dialogue_session("g1", user_qq="u2", now=101.0, ttl_seconds=240)

    marked = state.mark_dialogue_bot_reply(
        "g1",
        user_qq="u1",
        text="这是较早的一轮回复",
        now=102.0,
        ttl_seconds=240,
    )

    assert marked is False
    assert state.get_dialogue_session("g1", now=103.0)["user_qq"] == "u2"


def test_closing_reply_is_marked_to_end_the_session_after_answering():
    _open_replied_dialogue()

    decision = dialogue.evaluate_continuation(
        group_id="g1",
        user_qq="u1",
        text="懂了，谢谢",
        bot_qq="bot",
        now=110.0,
    )

    assert decision.candidate is True
    assert decision.close_after_reply is True
    assert dialogue.is_closing_message("谢谢") is True
