from __future__ import annotations

import nonebot

nonebot.init()

from src.plugins.xiaomo.config import load_config  # noqa: E402


def test_default_config_includes_active_group_ids():
    cfg = load_config()

    assert "1056259135" in cfg["allowed_group_ids"]
    assert "1070638552" in cfg["allowed_group_ids"]
    assert "972277179" in cfg["allowed_group_ids"]


def test_recruitment_group_has_scoped_policy_and_rate_limits():
    cfg = load_config()["group_policies"]["972277179"]

    assert cfg["self_reference"] == "小源"
    assert cfg["civil_language"]["enabled"] is True
    assert cfg["recruitment"]["website"] == "https://cdut-osa.cn"
    assert cfg["recruitment"]["append_on_relevant_topic"] is True
    assert cfg["proactive_join"]["score_bonus"] >= 15
    assert cfg["proactive_join"]["max_bot_messages_5m"] <= 3
    assert cfg["proactive_join"]["min_cooldown_seconds"] >= 45


def test_default_proactive_join_is_chatty_but_rate_limited():
    cfg = load_config()["proactive_join"]

    assert cfg["enabled"] is True
    assert cfg["min_cooldown_seconds"] <= 90
    assert cfg["window_seconds"] <= 1.0
    assert cfg["recent_context_messages"] >= 6
    assert cfg["local_reactions_enabled"] is True
    assert cfg["post_check"]["enabled"] is True
    assert 6 <= cfg["post_check"]["cancel_after_human_messages"] <= 10
    assert cfg["post_check"]["stale_seconds"] >= 45
    assert cfg["probability"]["react"] >= 0.5
    assert cfg["probability"]["short_reply"] >= 0.8
    assert cfg["probability"]["helpful_reply"] >= 0.9


def test_default_human_timing_keeps_mentions_fast():
    cfg = load_config()["human_timing"]

    assert cfg["enabled"] is True
    assert cfg["explicit_max_seconds"] <= 1.0
    assert cfg["proactive_max_seconds"] <= 1.5


def test_default_humanize_avoids_duplicate_ai_gate_for_proactive_join():
    cfg = load_config()["humanize"]

    assert cfg["strategy_llm_for_proactive"] is False
    assert cfg["proactive_fallback_max_delay_seconds"] <= 0.25


def test_default_config_prioritizes_text_chat_over_general_pokes():
    cfg = load_config()

    assert cfg["poke_everyone_cooldown_minutes"] == 0
