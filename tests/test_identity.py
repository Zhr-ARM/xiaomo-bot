from __future__ import annotations

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import database, handlers


def test_display_name_prefers_live_nickname_over_old_preferred_name():
    profile = {
        "qq_id": "1458741024",
        "nickname": "\u5929\u7167\u547d",
        "nicknames": ["\u52c7\u795e"],
        "profile": {"preferred_name": "\u52c7\u795e"},
    }

    assert handlers._display_name_from_profile(profile, "1458741024") == "\u5929\u7167\u547d"


def test_current_message_prompt_anchors_speaker_identity():
    text = handlers._format_current_user_message(
        user_display="\u5929\u7167\u547d",
        user_qq="1458741024",
        raw_text="sb",
        mode="normal",
    )

    assert "[CURRENT_SPEAKER]" in text
    assert "name: \u5929\u7167\u547d" in text
    assert "qq: 1458741024" in text
    assert "[CURRENT_MESSAGE][\u5929\u7167\u547d (QQ:1458741024)]: sb" in text
    assert "Do not rename this speaker" in text


@pytest.mark.asyncio
async def test_update_user_profile_refreshes_live_nickname(monkeypatch, tmp_path):
    await database.close_database()
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(tmp_path / "xiaomo-test.db")},
    )
    await database.init_database()

    try:
        await handlers._update_user_profile("u1", "old-name")
        await handlers._update_user_profile("u1", "new-name")

        async with await database.get_session() as session:
            profile = await database.get_user_profile_summary(session, "u1")

        assert profile["nickname"] == "new-name"
    finally:
        await database.close_database()
