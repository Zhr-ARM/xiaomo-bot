from __future__ import annotations

import nonebot

nonebot.init()
from src.plugins.xiaomo import interaction


def test_busy_group_and_recent_bot_reply_suppresses_proactive_interaction():
    decision = interaction.decide_interaction(
        interaction.InteractionSignals(
            reason="reaction",
            trigger_text="谢谢",
            candidate_text="不客气",
            messages_last_5m=18,
            seconds_since_bot_reply=20,
        )
    )

    assert decision.score < 40
    assert decision.action == "silent"


def test_help_request_scores_high_enough_for_brief_reply():
    decision = interaction.decide_interaction(
        interaction.InteractionSignals(
            reason="topic_engage",
            trigger_text="这个 bug 怎么修啊",
            candidate_text="要不要小源帮你看看日志？",
            messages_last_5m=2,
            seconds_since_bot_reply=600,
            topic_match=True,
            user_total_messages=30,
        )
    )

    assert decision.score >= 65
    assert decision.action in {"brief_reply", "normal_reply"}


def test_explicit_trigger_stays_replyable_even_when_group_is_busy():
    decision = interaction.decide_interaction(
        interaction.InteractionSignals(
            explicit_trigger=True,
            reason="mention",
            trigger_text="@小源 说话",
            candidate_text="来了",
            messages_last_5m=30,
            seconds_since_bot_reply=5,
        )
    )

    assert decision.score >= 65
    assert decision.action != "silent"


def test_interaction_decision_export_payload_is_stable():
    decision = interaction.decide_interaction(
        interaction.InteractionSignals(reason="bubble", candidate_text="有人吗")
    )

    payload = decision.to_payload()

    assert set(payload) == {"score", "action", "reason"}


def test_join_opportunity_scores_helpful_question_high():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u8fd9\u4e2a bug \u600e\u4e48\u4fee\u554a",
            topic_match=True,
            messages_last_5m=2,
            seconds_since_bot_reply=1200,
            human_messages_since_bot=8,
        )
    )

    assert decision.score >= 78
    assert decision.action == "helpful_reply"
    assert decision.max_chars == 360


def test_join_opportunity_stays_silent_when_busy_and_recent():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u8fd9\u4e2a bug \u600e\u4e48\u4fee\u554a",
            topic_match=True,
            messages_last_5m=22,
            bot_messages_last_5m=2,
            seconds_since_bot_reply=40,
            human_messages_since_bot=1,
        )
    )

    assert decision.action == "silent"
    assert decision.score < 45


def test_join_opportunity_promotes_normal_question_to_short_reply():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u8fd9\u4e2a\u65b9\u6848\u662f\u4e0d\u662f\u6709\u70b9\u5947\u602a",
            messages_last_5m=4,
            seconds_since_bot_reply=900,
            human_messages_since_bot=5,
        )
    )

    assert decision.action in {"short_reply", "helpful_reply"}
    assert decision.score >= 50


def test_join_opportunity_opinion_opening_gets_at_least_react():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u611f\u89c9\u8fd9\u4e2a\u65b9\u6848\u6709\u70b9\u5947\u602a",
            messages_last_5m=4,
            seconds_since_bot_reply=900,
            human_messages_since_bot=5,
        )
    )

    assert decision.action in {"react", "short_reply", "helpful_reply"}
    assert decision.score >= 36


def test_join_opportunity_social_opening_invites_short_reply():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u6709\u4eba\u6765\u804a\u804a\u8fd9\u4e2a\u65b9\u6848\u5417",
            messages_last_5m=2,
            seconds_since_bot_reply=1200,
            human_messages_since_bot=6,
        )
    )

    assert decision.action in {"short_reply", "helpful_reply"}
    assert decision.score >= 64


def test_join_opportunity_light_emotion_can_react_when_roomy():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u54c8\u54c8",
            messages_last_5m=1,
            seconds_since_bot_reply=1200,
            human_messages_since_bot=6,
        )
    )

    assert decision.action in {"react", "short_reply"}
    assert decision.score >= 32


def test_join_opportunity_respects_quiet_hours():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u6709\u4eba\u5728\u5417",
            quiet_hours=True,
            messages_last_5m=1,
            seconds_since_bot_reply=9999,
        )
    )

    assert decision.action == "silent"
    assert decision.reason == "quiet hours"


def test_join_opportunity_payload_includes_action_budget():
    decision = interaction.decide_join_opportunity(
        interaction.JoinOpportunitySignals(
            trigger_text="\u611f\u89c9\u8fd9\u4e2a\u65b9\u6848\u6709\u70b9\u5947\u602a",
            messages_last_5m=3,
            seconds_since_bot_reply=9999,
            human_messages_since_bot=5,
        )
    )

    payload = decision.to_payload()

    assert set(payload) == {"score", "action", "reason", "max_chars"}


def test_state_recorders_trim_five_minute_windows(monkeypatch):
    from src.plugins.xiaomo import state

    monkeypatch.setattr(state.time, "time", lambda: 1000.0)
    state.group_message_times["g1"] = [100.0, 800.0]
    state.bot_reply_times["g1"] = [200.0, 900.0]

    state.record_group_message("g1")
    state.record_bot_reply("g1")

    assert state.group_message_times["g1"] == [800.0, 1000.0]
    assert state.bot_reply_times["g1"] == [900.0, 1000.0]


def test_recent_group_flow_formats_live_context(monkeypatch):
    from src.plugins.xiaomo import state

    state.group_recent_texts.clear()
    monkeypatch.setattr(state.time, "time", lambda: 1000.0)

    state.record_recent_group_text(
        "g1", user_qq="u1", nickname="天照命", text="有人来聊聊这个方案吗"
    )
    state.record_recent_group_text(
        "g1", user_qq="u2", nickname="", text="我觉得有点怪"
    )

    flow = state.format_recent_group_flow("g1", limit=2)

    assert "[天照命]: 有人来聊聊这个方案吗" in flow
    assert "[QQu2]: 我觉得有点怪" in flow


def test_recent_group_flow_drops_expired_messages(monkeypatch):
    from src.plugins.xiaomo import state

    state.group_recent_texts.clear()
    state.record_recent_group_text(
        "g1", user_qq="u1", nickname="旧人", text="很早之前的话", now=80.0
    )
    state.record_recent_group_text(
        "g1", user_qq="u2", nickname="新人", text="刚刚这句", now=260.0
    )

    flow = state.format_recent_group_flow("g1", now=280.0)

    assert "很早之前" not in flow
    assert "刚刚这句" in flow
