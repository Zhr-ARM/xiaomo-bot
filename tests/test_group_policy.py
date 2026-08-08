from __future__ import annotations

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import delivery, group_policy, persona  # noqa: E402


def _policy_config() -> dict:
    return {
        "proactive_join": {
            "enabled": True,
            "min_cooldown_seconds": 90,
            "post_check": {"enabled": True, "stale_seconds": 55},
            "probability": {
                "react": 0.55,
                "short_reply": 0.82,
                "helpful_reply": 0.95,
            },
        },
        "group_policies": {
            "972277179": {
                "self_reference": "小源",
                "civil_language": {
                    "enabled": True,
                    "fallback": "这句容易伤人，小源只聊事情本身。",
                },
                "recruitment": {
                    "enabled": True,
                    "website": "https://cdut-osa.cn",
                    "append_on_relevant_topic": True,
                },
                "proactive_join": {
                    "min_cooldown_seconds": 60,
                    "score_bonus": 20,
                    "max_bot_messages_5m": 3,
                    "probability": {"react": 0.72},
                },
            }
        },
    }


def test_group_policy_deep_merges_proactive_overrides(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)

    config = group_policy.get_effective_proactive_join_config("972277179")

    assert config["min_cooldown_seconds"] == 60
    assert config["score_bonus"] == 20
    assert config["probability"]["react"] == 0.72
    assert config["probability"]["short_reply"] == 0.82
    assert config["post_check"]["stale_seconds"] == 55


def test_group_policy_rewrites_only_natural_first_person_text(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)
    content = (
        "我爱你，我们一起看；咱也来，本猫赞成，你我都懂；自我介绍不用重复。\n"
        "```python\nprint('我不应被改')\n```"
    )

    rewritten = group_policy.apply_outgoing_group_policy(content, "972277179")

    assert "小源爱你" in rewritten
    assert "小源和大家一起看" in rewritten
    assert "小源也来" in rewritten
    assert "小源赞成" in rewritten
    assert "你和小源都懂" in rewritten
    assert "自我介绍" in rewritten
    assert "print('我不应被改')" in rewritten
    assert group_policy.apply_outgoing_group_policy("我爱你", "other") == "我爱你"


def test_group_policy_blocks_clear_personal_attacks(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)

    rewritten = group_policy.apply_outgoing_group_policy(
        "都怪你，你真是个废物",
        "972277179",
    )

    assert rewritten == "这句容易伤人，小源只聊事情本身。"
    assert "废物" not in rewritten


def test_group_policy_keeps_neutral_explanations_of_an_insult(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)
    explanations = (
        "“SB”属于侮辱性表达，别这么称呼别人。",
        "你觉得“SB”是什么意思？",
    )

    for explanation in explanations:
        rewritten = group_policy.apply_outgoing_group_policy(
            explanation,
            "972277179",
        )

        assert rewritten == explanation


def test_group_instruction_contains_recruitment_and_conduct_rules(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)

    instruction = group_policy.build_group_policy_instruction("972277179")

    assert "所有自指都用“小源”" in instruction
    assert "不指责、羞辱" in instruction
    assert "https://cdut-osa.cn" in instruction
    assert "夜间无人说话时保持安静" in instruction


def test_group_policy_adds_recruitment_link_only_when_relevant(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)

    recruited = group_policy.apply_outgoing_group_policy(
        "体育部招新挺有活力的，新同学可以多看看。",
        "972277179",
        recent_bot_texts=[],
    )
    recently_shared = group_policy.apply_outgoing_group_policy(
        "社团招新开始热闹了。",
        "972277179",
        recent_bot_texts=["刚发过 https://cdut-osa.cn"],
    )
    unrelated = group_policy.apply_outgoing_group_policy(
        "今天食堂人好多。",
        "972277179",
        recent_bot_texts=[],
    )

    assert "小源也给开源协会打个招新" in recruited
    assert "https://cdut-osa.cn" in recruited
    assert "https://cdut-osa.cn" not in recently_shared
    assert "https://cdut-osa.cn" not in unrelated


def test_persona_receives_group_specific_policy(monkeypatch):
    monkeypatch.setattr(group_policy, "get_config", _policy_config)
    monkeypatch.setattr(persona, "_load_persona", lambda: "base persona")
    monkeypatch.setattr(persona, "_load_memory", lambda: "")

    prompt = persona.build_system_prompt(group_id="972277179")

    assert "base persona" in prompt
    assert "https://cdut-osa.cn" in prompt
    assert "所有自指都用“小源”" in prompt


@pytest.mark.asyncio
async def test_delivery_applies_group_policy_to_every_outbound_path(monkeypatch):
    sent = []

    class FakeBot:
        async def send_group_msg(self, **kwargs):
            sent.append(kwargs)
            return {"message_id": 123}

    monkeypatch.setattr(group_policy, "get_config", _policy_config)
    monkeypatch.setattr(
        delivery,
        "get_config",
        lambda: {"delivery": {"send_timeout_seconds": 1}},
    )

    source_id = await delivery.send_group_text(
        FakeBot(),
        "972277179",
        "我来啦",
        remember=False,
    )

    assert source_id == "123"
    assert sent == [{"group_id": 972277179, "message": "小源来啦"}]
