from __future__ import annotations

import nonebot

nonebot.init()

from src.plugins.xiaomo.tone_polisher import build_tone_instruction, polish_tone


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
