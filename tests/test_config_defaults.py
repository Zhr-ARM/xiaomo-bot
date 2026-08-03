from __future__ import annotations

from src.plugins.xiaomo.config import load_config


def test_default_config_includes_active_group_ids():
    cfg = load_config()

    assert "1056259135" in cfg["allowed_group_ids"]
    assert "1070638552" in cfg["allowed_group_ids"]


def test_default_proactive_join_is_chatty_but_rate_limited():
    cfg = load_config()["proactive_join"]

    assert cfg["enabled"] is True
    assert cfg["min_cooldown_seconds"] <= 180
    assert cfg["window_seconds"] <= 1.0
    assert cfg["recent_context_messages"] >= 6
    assert cfg["probability"]["short_reply"] >= 0.65
    assert cfg["probability"]["helpful_reply"] >= 0.85
