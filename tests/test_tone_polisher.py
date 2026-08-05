from __future__ import annotations

import nonebot

nonebot.init()

from src.plugins.xiaomo.tone_polisher import (
    build_adaptive_style_instruction,
    build_tone_instruction,
    polish_tone,
)


def test_polish_tone_removes_formal_answer_shell_for_casual_chat():
    reply = (
        "\u4ee5\u4e0b\u662f\u6211\u7684\u56de\u7b54\uff1a\n"
        "1. \u9996\u5148\uff0c\u522b\u76f4\u63a5\u628a\u6982\u7387\u8c03\u9ad8\u3002\n"
        "2. \u5176\u6b21\uff0c\u5148\u770b\u7fa4\u91cc\u662f\u4e0d\u662f\u8fd8\u5728\u804a\u3002"
    )

    polished = polish_tone(
        reply,
        scene="casual_question",
        style="brief",
        explicit_trigger=True,
        max_chars=220,
    )

    assert "\u4ee5\u4e0b\u662f" not in polished
    assert "1." not in polished
    assert "\n" not in polished
    assert len(polished) <= 220


def test_polish_tone_keeps_code_blocks_intact():
    reply = (
        "\u5f53\u7136\uff0c\u53ef\u4ee5\u8fd9\u6837\uff1a\n"
        "```python\n"
        "print('hi')\n"
        "```"
    )

    polished = polish_tone(
        reply,
        scene="technical_help",
        style="serious",
        explicit_trigger=True,
        max_chars=800,
    )

    assert "```python" in polished
    assert "print('hi')" in polished


def test_polish_tone_reduces_repeated_catchphrases():
    reply = "\u55b5\u55b5\u55b5\u55b5\uff5e\uff5e\uff5e\u8fd9\u4e2a\u786e\u5b9e\u5f97\u5148\u770b\u4e0a\u4e0b\u6587"

    polished = polish_tone(
        reply,
        scene="group_flow",
        style="playful",
        explicit_trigger=False,
        proactive=True,
        max_chars=300,
    )

    assert "\u55b5\u55b5\u55b5" not in polished
    assert "\uff5e\uff5e\uff5e" not in polished
    assert len(polished) <= 180


def test_tone_instruction_exposes_reply_budget():
    instruction = build_tone_instruction(
        scene="group_flow",
        style="brief",
        explicit_trigger=False,
        proactive=True,
        max_chars=160,
    )

    assert "[TONE_POLISH]" in instruction
    assert "max_chars: 160" in instruction
    assert "proactive_join: yes" in instruction


def test_adaptive_style_matches_short_group_rhythm_and_stops_question_reflex():
    instruction = build_adaptive_style_instruction(
        recent_group_messages=[
            {"text": "笑死"},
            {"text": "确实"},
            {"text": "刚装好"},
        ],
        recent_assistant_replies=[],
        current_text="我终于把环境装好了",
        speaker_name="Tony",
        scene="personal_share",
    )

    assert "6-35 字" in instruction
    assert "默认不要在结尾再抛一个问题" in instruction
    assert "不要顺手追加教程" in instruction
    assert "Tony同学" in instruction


def test_adaptive_style_detects_recent_roleplay_habits():
    instruction = build_adaptive_style_instruction(
        recent_group_messages=[],
        recent_assistant_replies=[
            "喵～（尾巴晃了晃）这个确实",
            "本猫也觉得离谱🐾",
        ],
        current_text="太抽象了",
        scene="casual_banter",
    )

    assert "喵、本猫、猫系颜文字" in instruction
    assert "括号里的耳朵尾巴动作" in instruction


def test_polish_tone_drops_repeated_roleplay_vocative_and_generic_followup():
    polished = polish_tone(
        "Tony同学，（悄悄说）猫猫也觉得这事离谱喵～！要不要再聊聊？",
        scene="personal_share",
        style="brief",
        explicit_trigger=True,
        max_chars=180,
        recent_assistant_replies=[
            "Tony，你说得对喵",
            "（尾巴晃了晃）本猫懂了🐾",
        ],
        speaker_name="Tony",
        current_text="这事真的太离谱了",
    )

    assert "同学" not in polished
    assert "耳朵" not in polished
    assert "悄悄说" not in polished
    assert "本猫" not in polished
    assert "猫猫" not in polished
    assert "喵" not in polished
    assert "要不要" not in polished


def test_decatify_keeps_a_separator_when_wave_joined_two_clauses():
    polished = polish_tone(
        "猫猫当然不在线下等你呀～小源只在群里在线（悄悄说）",
        scene="casual_question",
        style="brief",
        explicit_trigger=True,
        recent_assistant_replies=["本猫在窗台喵", "猫猫刚刚探头了🐾"],
        current_text="我怎么没在6B504看到你",
    )

    assert "呀，小源" in polished
    assert "悄悄说" not in polished
