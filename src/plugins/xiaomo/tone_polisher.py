"""Final tone shaping for group-chat replies.

The polisher is deliberately local and conservative. It removes common
"assistant answer" habits, trims casual replies, and softens formal phrasing
without changing the factual content of the answer.
"""
from __future__ import annotations

import re
from statistics import median

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
    "[当下群聊语感]",
)

_CAT_STYLE_RE = re.compile(r"喵|本猫|猫猫|猫娘|🐾|ΦωΦ|=\^･?ω･?\^=")
_STAGE_ACTION_RE = re.compile(
    r"[（(][^（）()\n]{0,40}(?:耳朵|尾巴|爪子|歪头|眨眼|炸毛|打盹|蹭了蹭|甩了甩|"
    r"悄悄说|小声说|认真脸|摊手|捂脸|扶额|探头|冒头|偷笑|叹气|点头)[^（）()\n]{0,40}[）)]"
)
_FORCED_TECH_METAPHOR_RE = re.compile(
    r"(?:像|比).{0,18}(?:PID|debug|bug|板子|电压|电流|寄存器|示波器)",
    flags=re.I,
)
_QUESTION_START_RE = re.compile(
    r"^(?:怎么|咋|为什么|为何|谁|哪|什么|多少|几|能不能|可不可以|有没有|是不是)"
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
        "Casual chat should be one or two short sentences. Pick one conversational "
        "move (react, answer, add one thought, or ask one necessary question) instead "
        "of stacking all four. A statement usually does not need a question at the end.\n"
        "[/TONE_POLISH]"
    )


def _looks_like_question(text: str) -> bool:
    clean = (text or "").strip()
    if not clean:
        return False
    if "?" in clean or "？" in clean:
        return True
    if re.search(r"(?:吗|么|嘛|呢)$", clean):
        return True
    if _QUESTION_START_RE.match(clean):
        return True
    return bool(
        re.search(
            r"(?:^|[我你他她它这那]).{0,8}(?:怎么|咋|为什么|为何|谁|哪(?:里|个)?|什么|多少)",
            clean,
        )
    )


def _observed_message_lengths(recent_group_messages: list[dict] | None) -> list[int]:
    lengths: list[int] = []
    for item in (recent_group_messages or [])[-12:]:
        text = str(item.get("text") or "").strip()
        if not text or text.startswith("[") and text.endswith("]"):
            continue
        text = re.sub(r"https?://\S+", "链接", text)
        lengths.append(min(len(text), 240))
    return lengths


def build_adaptive_style_instruction(
    *,
    recent_group_messages: list[dict] | None,
    recent_assistant_replies: list[str] | None,
    current_text: str,
    speaker_name: str = "",
    scene: str = "group_flow",
) -> str:
    """Describe the local conversational rhythm without copying user content."""

    lines = ["[当下群聊语感]"]
    lengths = _observed_message_lengths(recent_group_messages)
    if lengths and scene not in {"technical_help", "live_info", "weather"}:
        middle = int(median(lengths))
        if middle <= 12:
            lines.append("- 最近大家主要发碎片短句；这轮闲聊尽量落在 6-35 字，能半句接住就别写满两句。")
        elif middle <= 32:
            lines.append("- 最近群聊节奏偏短；这轮闲聊通常控制在 12-70 字。")
        else:
            lines.append("- 最近消息稍长，可以说完整一点，但只保留当前话题需要的内容。")

    if not _looks_like_question(current_text) and scene not in {"technical_help", "image_question"}:
        lines.append("- 当前更像陈述、分享或吐槽；接住即可，默认不要在结尾再抛一个问题。")
    if scene == "personal_share":
        lines.append("- 这是在分享近况，不是在求助；不要顺手追加教程、提醒、排障建议或‘下次记得’。")
    elif scene == "social_ack":
        lines.append("- 这是感谢、问候或确认；回半句就收住，不开启新话题。")

    recent = [str(text) for text in (recent_assistant_replies or [])[-6:] if str(text).strip()]
    overused: list[str] = []
    if sum(bool(_CAT_STYLE_RE.search(text)) for text in recent) >= 2:
        overused.append("喵、本猫、猫系颜文字")
    if any(_STAGE_ACTION_RE.search(text) for text in recent):
        overused.append("括号里的耳朵尾巴动作")
    if sum(bool(_FORCED_TECH_METAPHOR_RE.search(text)) for text in recent) >= 2:
        overused.append("硬塞技术比喻")
    if overused:
        lines.append(f"- 最近小源已经反复用过{'、'.join(overused)}；这一轮全部避开。")

    clean_name = re.sub(r"[\r\n\[\]]+", " ", (speaker_name or "")).strip()[:40]
    if clean_name and not clean_name.startswith("QQ"):
        lines.append(f"- 正在顺着 {clean_name} 的话接，不必先喊“{clean_name}同学”，直接说内容更自然。")
    lines.extend(
        [
            "- 只选一个主要动作：接梗、回答、补一句看法、或问一个必要问题；不要写成‘反应+解释+建议+反问’全套。",
            "- 跟随的是句长和松紧，不模仿错别字、脏话或攻击性表达。",
            "[/当下群聊语感]",
        ]
    )
    return "\n".join(lines)


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


def _recent_habit_count(replies: list[str] | None, pattern: re.Pattern) -> int:
    return sum(bool(pattern.search(str(reply))) for reply in (replies or [])[-6:])


def _decatify(text: str) -> str:
    cleaned = _STAGE_ACTION_RE.sub("", text)
    cleaned = cleaned.replace("本猫", "我").replace("猫猫", "我").replace("🐾", "")
    cleaned = re.sub(r"喵(?=[～~，,。.!！?？\s]|$)", "", cleaned)
    cleaned = re.sub(r"[（(]\s*(?:ΦωΦ|=\^･?ω･?\^=)\s*[）)]", "", cleaned)
    cleaned = re.sub(r"[～~]+", "", cleaned)
    return cleaned


def _soften_repeated_vocative(
    text: str,
    *,
    speaker_name: str,
    recent_assistant_replies: list[str] | None,
) -> str:
    name = (speaker_name or "").strip()
    if not name or name.startswith("QQ"):
        return text
    escaped = re.escape(name)
    cleaned = re.sub(fr"^{escaped}同学(?=[，,：:\s])", name, text)
    recently_used = any(
        re.match(fr"^\s*{escaped}(?:同学)?[，,：:\s]", str(reply))
        for reply in (recent_assistant_replies or [])[-6:]
    )
    if recently_used:
        without = re.sub(fr"^\s*{escaped}(?:同学)?[，,：:\s]+", "", cleaned)
        if without.strip():
            cleaned = without
    return cleaned


def _drop_generic_followup(text: str, *, current_text: str, style: str) -> str:
    if style == "ask_back" or _looks_like_question(current_text):
        return text
    pattern = re.compile(
        r"^(?P<body>.+[。！!])(?P<question>[^。！!?？\n]{0,36}"
        r"(?:要不要|你呢|咋样|怎么样|说来听听|觉得呢|对吧|是吧|需要我|想聊聊)"
        r"[^。！!?？\n]*[？?])$",
        flags=re.S,
    )
    match = pattern.match(text.strip())
    return match.group("body").strip() if match else text


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
    recent_assistant_replies: list[str] | None = None,
    speaker_name: str = "",
    current_text: str = "",
) -> str:
    text = _strip_leakage(reply or "")
    if not text:
        return text

    has_code = _has_code_or_table(text)
    text = _strip_formal_prefix(text)
    text = _soften_formal_phrases(text)
    text = _reduce_verbal_tics(text)

    current_cat_count = len(_CAT_STYLE_RE.findall(text))
    if current_cat_count > 1 or _recent_habit_count(
        recent_assistant_replies, _CAT_STYLE_RE
    ) >= 2:
        text = _decatify(text)
    elif len(_STAGE_ACTION_RE.findall(text)) > 1 or _recent_habit_count(
        recent_assistant_replies, _STAGE_ACTION_RE
    ):
        text = _STAGE_ACTION_RE.sub("", text)

    text = _soften_repeated_vocative(
        text,
        speaker_name=speaker_name,
        recent_assistant_replies=recent_assistant_replies,
    )
    text = _drop_generic_followup(text, current_text=current_text, style=style)

    if not has_code:
        text = _remove_empty_markdown_shell(text)

    casual = scene in {
        "casual_banter",
        "casual_question",
        "group_flow",
        "personal_share",
        "social_ack",
    }
    if proactive:
        max_chars = min(max_chars, 180)
    if casual and not has_code:
        text = _collapse_casual_lines(text, max_lines=2)
        max_chars = min(max_chars, 180 if proactive else 220)
    elif style in {"brief", "playful", "ask_back"}:
        max_chars = min(max_chars, 260)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([，。！？!?])", r"\1", text).strip()
    return trim_to_length(text, max_chars)
