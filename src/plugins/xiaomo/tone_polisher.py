"""Final tone shaping for group-chat replies.

The polisher is deliberately local and conservative. It removes common
"assistant answer" habits, trims casual replies, and softens formal phrasing
without changing the factual content of the answer.
"""
from __future__ import annotations

import re

from .filter_utils import trim_to_length


FORMAL_PREFIX_PATTERNS = (
    r"^(?:好的|当然|可以|没问题)[，,。!\s]*(?:我来|我可以|下面|以下)?\s*",
    r"^(?:以下是|下面是)(?:我的)?(?:回答|建议|分析)[:：，,\s]*",
    r"^(?:作为(?:一个)?AI(?:助手)?|作为(?:一个)?机器人)[，,。].*?[。.!！]\s*",
)

FORMAL_REPLACEMENTS = (
    ("首先，", "先说结论，"),
    ("首先,", "先说结论，"),
    ("其次，", "然后，"),
    ("最后，", "最后，"),
    ("综上所述，", "所以大概就是，"),
    ("需要注意的是，", "这里注意下，"),
    ("建议你", "可以先"),
    ("我建议", "我觉得可以"),
    ("请确认", "先确认"),
    ("无法为您", "这个我不太能"),
    ("用户", "你"),
)

LEAKY_MARKERS = (
    "[CURRENT_SPEAKER]",
    "[CURRENT_MESSAGE]",
    "[RECENT_BATCH_CONTEXT]",
    "[回复策略]",
    "[群聊理解]",
    "[PROACTIVE_JOIN]",
    "[TONE_POLISH]",
)


def build_tone_instruction(
    *,
    scene: str,
    style: str,
    explicit_trigger: bool,
    proactive: bool,
    max_chars: int,
) -> str:
    trigger_hint = "explicit" if explicit_trigger else "ambient"
    proactive_hint = "yes" if proactive else "no"
    return (
        "[TONE_POLISH]\n"
        f"scene: {scene}\n"
        f"style: {style}\n"
        f"trigger: {trigger_hint}\n"
        f"proactive_join: {proactive_hint}\n"
        f"max_chars: {max_chars}\n"
        "Sound like a real group member, not a formal assistant. "
        "Start by reacting to the context, then answer. Avoid essay openings, "
        "self-introduction, repeated catchphrases, and forced cuteness. "
        "Casual chat should be one or two short sentences.\n"
        "[/TONE_POLISH]"
    )


def _has_code_or_table(text: str) -> bool:
    if "```" in text:
        return True
    if re.search(r"^\s*\|.+\|\s*$", text, flags=re.M):
        return True
    return bool(re.search(r"^\s{4,}\S", text, flags=re.M))


def _strip_leakage(text: str) -> str:
    cleaned = text.strip()
    for marker in LEAKY_MARKERS:
        cleaned = cleaned.replace(marker, "")
        cleaned = cleaned.replace(marker.replace("[", "[/"), "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_formal_prefix(text: str) -> str:
    cleaned = text.strip()
    for pattern in FORMAL_PREFIX_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.I)
    return cleaned.strip()


def _soften_formal_phrases(text: str) -> str:
    softened = text
    for old, new in FORMAL_REPLACEMENTS:
        softened = softened.replace(old, new)
    return softened


def _collapse_casual_lines(text: str, *, max_lines: int = 2) -> str:
    lines = [
        re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    if not lines:
        return text.strip()
    return " ".join(lines[:max_lines]).strip()


def _reduce_verbal_tics(text: str) -> str:
    cleaned = re.sub(r"(喵[呜呜~～]*){3,}", "喵呜", text)
    cleaned = re.sub(r"(哈[哈啊呀]*){4,}", "哈哈", cleaned)
    cleaned = re.sub(r"(～|~){3,}", "～", cleaned)
    cleaned = re.sub(r"([。！？!?])\1{2,}", r"\1", cleaned)
    cleaned = re.sub(r"(喵[。!！~～]*)\s*(喵[。!！~～]*)+", r"\1", cleaned)
    return cleaned


def _remove_empty_markdown_shell(text: str) -> str:
    cleaned = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.M)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    return cleaned.strip()


def polish_tone(
    reply: str,
    *,
    scene: str,
    style: str,
    explicit_trigger: bool,
    proactive: bool = False,
    max_chars: int = 800,
) -> str:
    text = _strip_leakage(reply or "")
    if not text:
        return text

    has_code = _has_code_or_table(text)
    text = _strip_formal_prefix(text)
    text = _soften_formal_phrases(text)
    text = _reduce_verbal_tics(text)

    if not has_code:
        text = _remove_empty_markdown_shell(text)

    casual = scene in {"casual_banter", "casual_question", "group_flow"}
    if proactive:
        max_chars = min(max_chars, 180)
    if casual and not has_code:
        text = _collapse_casual_lines(text, max_lines=2)
        max_chars = min(max_chars, 180 if proactive else 220)
    elif style in {"brief", "playful", "ask_back"}:
        max_chars = min(max_chars, 260)

    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return trim_to_length(text, max_chars)
