from __future__ import annotations

import pytest
import nonebot

nonebot.init()
from src.plugins.xiaomo import humanize


def test_parse_strategy_reply_handles_fenced_json_and_clamps_delay():
    fallback = humanize.ReplyStrategy()

    strategy = humanize.parse_strategy_reply(
        """```json
        {"should_reply": true, "reply_style": "brief", "delay_seconds": 99,
         "scene_label": "technical_help", "warmth": "familiar",
         "instruction": "短一点，像顺手接话。", "reason": "明确提问"}
        ```""",
        fallback=fallback,
        max_delay=4.0,
    )

    assert strategy.should_reply is True
    assert strategy.reply_style == "brief"
    assert strategy.delay_seconds == 4.0
    assert strategy.scene_label == "technical_help"
    assert strategy.warmth == "familiar"
    assert "短一点" in strategy.instruction


def test_parse_strategy_reply_rejects_invalid_style():
    fallback = humanize.ReplyStrategy(reply_style="brief", instruction="fallback")

    strategy = humanize.parse_strategy_reply(
        '{"should_reply": true, "reply_style": "essay", "delay_seconds": 1}',
        fallback=fallback,
    )

    assert strategy.reply_style == "brief"
    assert strategy.instruction == "fallback"


def test_fallback_strategy_uses_profile_warmth_and_short_style():
    strategy = humanize.fallback_strategy(
        raw_text="小源 这个怎么修",
        context="成员在聊 bug 和串口",
        user_profile={"exists": True, "total_messages": 80},
        mode="normal",
    )

    assert strategy.should_reply is True
    assert strategy.warmth == "familiar"
    assert strategy.reply_style in {"brief", "serious"}
    assert strategy.delay_seconds > 0


def test_casual_why_question_is_not_mistaken_for_technical_help():
    strategy = humanize.fallback_strategy(
        raw_text="我怎么没在6B504看到你",
        context="",
        user_profile={"exists": True, "total_messages": 5},
    )

    assert strategy.reply_style == "brief"
    assert strategy.scene_label == "casual_chat"


def test_anxiety_uses_supportive_style():
    strategy = humanize.fallback_strategy(
        raw_text="要考试了好焦虑",
        context="",
        user_profile={"exists": True, "total_messages": 5},
    )

    assert strategy.reply_style == "supportive"
    assert strategy.scene_label == "support"


def test_humanize_instruction_encourages_brief_human_reply():
    strategy = humanize.ReplyStrategy(
        reply_style="brief",
        scene_label="casual_chat",
        warmth="regular",
        instruction="只接一句，不要展开。",
    )

    text = humanize.build_humanize_instruction(strategy)

    assert "只接一句" in text
    assert "不要把每次回复都写成完整答案" in text
    assert "一轮只做一个主要动作" in text
    assert "casual_chat" in text


def test_shape_reply_trims_brief_reply():
    strategy = humanize.ReplyStrategy(reply_style="brief")
    long_reply = "这是一段很长的回复。" * 80

    shaped = humanize.shape_reply(strategy, long_reply, default_max_chars=800)

    assert len(shaped) <= 260


@pytest.mark.asyncio
async def test_decide_reply_strategy_uses_llm_json():
    class FakeLLM:
        async def chat(self, **kwargs):
            assert "回复策略决策" in kwargs["user_message"]
            return '{"should_reply": false, "reply_style": "silent", "delay_seconds": 0, "reason": "只是路过提到名字"}'

    strategy = await humanize.decide_reply_strategy(
        llm=FakeLLM(),
        group_id="g1",
        raw_text="刚才小源那个配置",
        context="大家在讨论旧配置，不是在问机器人",
        user_profile={"exists": True, "total_messages": 2},
        mode="normal",
    )

    assert strategy.should_reply is False
    assert strategy.reply_style == "silent"


@pytest.mark.asyncio
async def test_decide_reply_strategy_falls_back_on_llm_error():
    class BrokenLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("boom")

    strategy = await humanize.decide_reply_strategy(
        llm=BrokenLLM(),
        group_id="g1",
        raw_text="小源 帮我看看这个 bug",
        context="",
        user_profile={"exists": True, "total_messages": 1},
        mode="normal",
    )

    assert strategy.should_reply is True
    assert strategy.reply_style in {"brief", "serious"}


@pytest.mark.asyncio
async def test_explicit_mention_uses_local_strategy_without_llm(monkeypatch):
    monkeypatch.setattr(
        humanize,
        "get_config",
        lambda: {"humanize": {"enabled": True, "strategy_llm_for_explicit": False}},
    )

    class FailIfCalled:
        async def chat(self, **kwargs):
            raise AssertionError("explicit mentions should not call strategy LLM")

    strategy = await humanize.decide_reply_strategy(
        llm=FailIfCalled(),
        group_id="g1",
        raw_text="[有成员@了小源]",
        context="",
        user_profile={"exists": True, "total_messages": 10},
        mode="normal",
        explicit_trigger=True,
    )

    assert strategy.should_reply is True
    assert strategy.reply_style == "brief"


@pytest.mark.asyncio
async def test_explicit_mention_caps_local_thinking_delay(monkeypatch):
    monkeypatch.setattr(
        humanize,
        "get_config",
        lambda: {
            "humanize": {
                "enabled": True,
                "strategy_llm_for_explicit": False,
                "explicit_max_delay_seconds": 0.25,
            }
        },
    )

    class FailIfCalled:
        async def chat(self, **kwargs):
            raise AssertionError("explicit mentions should not call strategy LLM")

    strategy = await humanize.decide_reply_strategy(
        llm=FailIfCalled(),
        group_id="g1",
        raw_text="bug 怎么修",
        context="",
        user_profile={"exists": True, "total_messages": 10},
        mode="normal",
        explicit_trigger=True,
    )

    assert strategy.delay_seconds <= 0.25


@pytest.mark.asyncio
async def test_proactive_strategy_uses_fast_local_shape_before_generation_ai(monkeypatch):
    monkeypatch.setattr(
        humanize,
        "get_config",
        lambda: {
            "humanize": {
                "enabled": True,
                "strategy_llm_for_proactive": False,
                "proactive_fallback_max_delay_seconds": 0.15,
            }
        },
    )

    class FailIfCalled:
        async def chat(self, **kwargs):
            raise AssertionError("proactive strategy must not add a second LLM call")

    strategy = await humanize.decide_reply_strategy(
        llm=FailIfCalled(),
        group_id="g1",
        raw_text="我们专业机械制图居然不教 CAD",
        context="群里正在聊课程安排",
        user_profile={"exists": True, "total_messages": 5},
        proactive=True,
    )

    assert strategy.should_reply is True
    assert strategy.delay_seconds <= 0.15
