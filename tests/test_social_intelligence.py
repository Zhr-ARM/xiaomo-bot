from __future__ import annotations

import nonebot

nonebot.init()

from src.plugins.xiaomo import intelligence


def test_weather_plan_wins_over_mention_search_candidate():
    text = "\u4eca\u5929\u6210\u90fd\u5929\u6c14\u600e\u4e48\u6837"

    plan = intelligence.plan_tools(
        text,
        explicit_trigger=True,
        existing_search_query=text,
        existing_weather_query=text,
    )

    assert plan.needs_weather is True
    assert plan.needs_search is False
    assert plan.weather_query == text


def test_plain_mention_does_not_trigger_search():
    text = "\u4f60\u5728\u5417"

    plan = intelligence.plan_tools(
        text,
        explicit_trigger=True,
        existing_search_query=text,
    )

    assert plan.needs_search is False
    assert plan.needs_weather is False


def test_explicit_search_requires_search_tool():
    text = "\u641c\u7d22 Python \u6700\u65b0\u7248\u672c"

    plan = intelligence.plan_tools(
        text,
        explicit_trigger=True,
        existing_search_query=text,
    )

    assert plan.needs_search is True
    assert plan.search_required is True
    assert plan.search_query == text


def test_live_question_mentioned_uses_search_tool():
    text = "\u4eca\u5929\u6210\u90fd\u6709\u4ec0\u4e48\u65b0\u95fb"

    plan = intelligence.plan_tools(
        text,
        explicit_trigger=True,
        existing_search_query=text,
    )

    assert plan.needs_search is True
    assert plan.search_required is True


def test_casual_frame_keeps_reply_short_and_tool_free():
    frame = intelligence.build_conversation_frame(
        current_msg={"user_qq": "u1"},
        raw_text="\u4f60\u5728\u5417",
        explicit_trigger=True,
        search_query="\u4f60\u5728\u5417",
    )

    assert frame.scene == "casual_question"
    assert frame.max_chars <= 180
    assert frame.tool_plan is not None
    assert frame.tool_plan.needs_search is False


def test_post_check_strips_prompt_leakage_and_trims():
    frame = intelligence.ConversationFrame(
        current_user_qq="u1",
        current_text="\u4f60\u5728\u5417",
        explicit_trigger=True,
        scene="casual_question",
        tone="playful_brief",
        reply_goal="brief",
        max_chars=40,
    )

    reply = intelligence.post_check_reply(
        "[CURRENT_SPEAKER]\n" + ("\u8fd9\u6bb5\u4e0d\u8be5\u9732\u51fa\u6765\uff0c\u987a\u624b\u63a5\u4e00\u53e5\u5c31\u884c" * 5),
        frame=frame,
        style="brief",
        default_max_chars=800,
    )

    assert "[CURRENT_SPEAKER]" not in reply
    assert len(reply) <= 43
