"""小源 QQ 机器人 - 消息处理器

开源协会顾问猫娘 — 仅限群聊，不接受私聊。
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time as _time
from pathlib import Path

from nonebot import on_message, on_notice, get_bot
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, NoticeEvent

from . import state
from .config import get_config
from sqlalchemy import select

from .database import (
    Nickname,
    User,
    UserNickname,
    get_message_by_source_id,
    init_database,
    get_session,
    get_user_display_names,
    get_user_profile_summary,
    update_message_content,
)
from .delivery import send_group_text
from .dialogue import (
    ContinuationDecision,
    build_continuation_instruction,
    evaluate_continuation,
    is_closing_message,
)
from .llm import get_llm
from .memory import (
    build_context,
    forget_inbound_memory,
    group_key,
    schedule_memory_compression,
    schedule_vector_refresh,
    store_inbound_memory,
)
from .window import get_silent_window
from .auto_action import (
    check_reaction,
    check_repeat,
    detect_interesting_topic,
    is_quiet_hours,
    is_auto_poke_target,
    should_send_proactive_message,
    try_poke_topic,
)
from .humanize import (
    build_humanize_instruction,
    decide_reply_strategy,
    fallback_strategy,
    shape_reply,
)
from .group_policy import (
    apply_outgoing_group_policy,
    get_effective_proactive_join_config,
)
from .intelligence import (
    build_conversation_frame,
    build_frame_instruction,
    post_check_reply,
)
from .interaction import JoinOpportunitySignals, decide_join_opportunity
from .persona import build_system_prompt
from .tone_polisher import (
    build_adaptive_style_instruction,
    build_tone_instruction,
    polish_tone,
)
from .weather import query_weather
from .web_search import run_smart_search_result

logger = logging.getLogger("xiaomo.handlers")


def _trace_proactive(event: str, group_id: str, **details) -> None:
    """Write compact proactive diagnostics to the redirected runtime log."""
    fields = []
    for key, value in details.items():
        clean = str(value).replace("\r", " ").replace("\n", " ")[:160]
        fields.append(f"{key}={clean}")
    suffix = f" {' '.join(fields)}" if fields else ""
    print(f"[PROACTIVE] event={event} group={group_id}{suffix}", flush=True)

PROFILE_TOPIC_KEYWORDS = (
    ("python", "Python"),
    ("linux", "Linux"),
    ("github", "GitHub"),
    ("git", "Git"),
    ("单片机", "单片机"),
    ("嵌入式", "嵌入式"),
    ("stm32", "STM32"),
    ("esp32", "ESP32"),
    ("ai", "AI"),
    ("人工智能", "AI"),
    ("模型", "模型"),
    ("代码", "编程"),
    ("编程", "编程"),
    ("bug", "调试"),
    ("报错", "调试"),
    ("游戏", "游戏"),
    ("动漫", "动漫"),
    ("考试", "考试"),
)

PROFILE_STYLE_CUES = (
    (("哈哈", "笑死", "绷不住"), "爱开玩笑"),
    (("草", "离谱", "抽象"), "喜欢吐槽"),
    (("救命", "不会了", "卡住"), "遇到问题会直接求助"),
    (("谢谢", "感谢", "thx"), "会礼貌反馈"),
    (("熬夜", "睡不着", "通宵"), "经常聊熬夜状态"),
)

LOCAL_LIGHT_REACTION_RULES = (
    (("哈哈", "笑死", "绷不住"), ("笑死", "这句有点绷不住")),
    (("寄了", "寄寄"), ("这下真有点寄", "先别急着寄")),
    (("有人吗", "有人在吗", "有人来", "聊聊"), ("在，刚探头", "在呢，怎么个事")),
    (("离谱", "抽象"), ("确实有点离谱", "这个有点抽象")),
    (("草",), ("草，懂了", "草")),
    (("累", "困", "顶不住"), ("先缓口气", "感觉该歇一下了")),
)

# 天气查询关键词（显式 + 隐晦）
_WEATHER_PATTERNS = [
    "天气", "天气预报",
    "冷不冷", "热不热", "冷吗", "热吗",
    "下雨", "带伞", "雨伞", "会不会下",
    "几度", "多少度", "温度", "气温",
    "降温", "升温", "变天",
    "刮风", "风大", "大风",
    "雾霾", "雾", "霾",
    "出太阳", "晴天", "阴天",
    "外面冷", "外面热",
]


def _is_weather_query(text: str) -> str | None:
    """检测是否为天气查询"""
    if not text:
        return None
    t = text.lower()
    for kw in _WEATHER_PATTERNS:
        if kw in t:
            return text
    return None


def _detect_special_mode(text: str) -> tuple[str, str, str]:
    """检测特殊指令：(mode, target_name, cleaned_text)
    mode: 'praise' | 'roast' | 'joke' | 'normal'
    target_name: 夸夸/点草的目标人名
    """
    t = text.strip()

    # 冷笑话
    if any(kw in t for kw in ["来个笑话", "冷笑话", "讲个笑话"]):
        return ("joke", "", "")

    # 夸夸
    m = re.match(r"夸夸\s*(.+)", t)
    if m:
        return ("praise", m.group(1).strip(), "")

    # 点草
    m = re.match(r"点草\s*(.+)", t)
    if m:
        return ("roast", m.group(1).strip(), "")

    return ("normal", "", text)


def _detect_mood(reply: str) -> tuple[str, float] | None:
    """从回复文本中检测当前情绪，用于跨轮次角色连贯。
    返回 (mood, strength) 或 None（无法判断时）。
    """
    if not reply or len(reply) < 5:
        return None

    mood_scores = {
        "snarky": 0,
        "playful": 0,
        "gentle": 0,
        "energetic": 0,
        "elegant": 0,
        "cute": 0,
    }

    snarky_kw = ["(｀・ω・´)", "(ΦωΦ)", "你才", "你自己", "忘了？", "说谁呢", "拆台"]
    mood_scores["snarky"] = sum(1 for kw in snarky_kw if kw in reply)

    playful_kw = ["诶～", "233", "哈哈", "草", "好家伙", "装傻", "翻车", "不点名"]
    mood_scores["playful"] = sum(1 for kw in playful_kw if kw in reply)

    gentle_kw = ["温柔", "抱抱", "摸摸头", "不哭", "没关系", "加油", "坚持", "没事的", "慢慢来"]
    mood_scores["gentle"] = sum(1 for kw in gentle_kw if kw in reply)

    energetic_kw = ["！！", "来了来了", "冲！", "哇", "太好了", "恭喜", "厉害", "欢迎", "太强了"]
    mood_scores["energetic"] = sum(1 for kw in energetic_kw if kw in reply)

    elegant_kw = ["建议", "首先", "然后", "检查一下", "确认", "配置", "数据", "步骤", "可以试试", "注意", "其实"]
    mood_scores["elegant"] = sum(1 for kw in elegant_kw if kw in reply)

    cute_kw = ["(=^･ω･^=)", "(´;ω;`)", "(〃∀〃)", "撒娇", "蹭蹭", "喵呜", "咪"]
    mood_scores["cute"] = sum(1 for kw in cute_kw if kw in reply)

    best_mood = max(mood_scores, key=mood_scores.get)
    best_score = mood_scores[best_mood]

    if best_score >= 2:
        return (best_mood, min(best_score / 5.0, 1.0))
    return None


async def _ensure_init():
    if not state.db_initialized:
        await init_database()
        state.db_initialized = True


def _is_allowed_group(group_id: str) -> bool:
    allowed = get_config().get("allowed_group_ids", [])
    if not allowed:
        return True
    return group_id in allowed


def _display_name_from_profile(profile: dict, qq_id: str | None) -> str:
    if profile.get("nickname"):
        return str(profile["nickname"])
    pref = profile.get("profile", {}).get("preferred_name", "")
    if pref:
        return str(pref)
    nicknames = profile.get("nicknames", [])
    if nicknames:
        return str(nicknames[0])
    return f"QQ{qq_id}" if qq_id else "unknown member"


def _format_current_user_message(
    *,
    user_display: str,
    user_qq: str | None,
    raw_text: str,
    mode: str = "normal",
    batch_context: str = "",
) -> str:
    qq = user_qq or "unknown"
    context_block = ""
    if batch_context:
        context_block = (
            "\n\n[RECENT_BATCH_CONTEXT]\n"
            "These messages arrived in the same short group window. "
            "Use them only as immediate background; the current speaker below is the reply target.\n"
            f"{batch_context}\n"
            "[/RECENT_BATCH_CONTEXT]\n"
        )
    return (
        "[CURRENT_SPEAKER]\n"
        f"name: {user_display}\n"
        f"qq: {qq}\n"
        f"mode: {mode}\n"
        "Identity rule: QQ is the unique identity key. Names are display labels only. "
        "Never transfer memories, quotes, traits, or relationships from another QQ, even when names match.\n"
        "Important: The message below is the only current user input. "
        "Do not rename this speaker from chat history, old aliases, or other members.\n"
        "[/CURRENT_SPEAKER]\n\n"
        f"{context_block}"
        f"[CURRENT_MESSAGE][{user_display} (QQ:{qq})]: {raw_text}"
    )


def _select_current_message(messages: list[dict]) -> dict:
    """Pick the message the bot should answer in a silent-window batch."""
    if not messages:
        return {}

    def score(index: int, msg: dict) -> tuple[int, float, int]:
        value = 0
        if msg.get("explicit_trigger") or msg.get("dialogue_followup"):
            value += 100
        if msg.get("mode", "normal") != "normal":
            value += 80
        if (msg.get("search_text") or "").strip():
            value += 20
        if (msg.get("text") or "").strip():
            value += 5
        try:
            ts = float(msg.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        return value, ts, index

    return max(enumerate(messages), key=lambda item: score(item[0], item[1]))[1]


def _display_for_message(msg: dict, display_names: dict[str, str]) -> str:
    qq = str(msg.get("user_qq") or "")
    if qq and qq in display_names:
        return display_names[qq]
    return f"QQ{qq}" if qq else "未知成员"


def _format_batch_context(
    messages: list[dict],
    display_names: dict[str, str],
    current_msg: dict,
) -> str:
    if len(messages) <= 1:
        return ""

    lines = []
    for idx, msg in enumerate(messages, 1):
        qq = str(msg.get("user_qq") or "unknown")
        name = _display_for_message(msg, display_names)
        text = (msg.get("text") or "").strip() or "[空消息]"
        image_count = len(msg.get("images") or [])
        if image_count:
            image_label = f"[发送了{image_count}张图片]"
            if text in {"[非文本群消息]", "[有成员@了小源]", "[空消息]"}:
                text = image_label
            else:
                text = f"{text} {image_label}"
        marker = " <= reply target" if msg is current_msg else ""
        lines.append(f"{idx}. [{name} (QQ:{qq})]{marker}: {text}")
    return "\n".join(lines)


def _select_search_text(messages: list[dict], current_msg: dict) -> str:
    return _select_batch_field(messages, current_msg, "search_text")


def _select_weather_query(messages: list[dict], current_msg: dict) -> str:
    return _select_batch_field(messages, current_msg, "weather_query")


def _select_batch_images(messages: list[dict], current_msg: dict) -> list[dict]:
    """Carry images only within the selected speaker's turn."""
    current_images = list(current_msg.get("images") or [])
    if current_images:
        return current_images

    current_user = str(current_msg.get("user_qq") or "")
    for msg in reversed(messages):
        if str(msg.get("user_qq") or "") != current_user:
            continue
        images = list(msg.get("images") or [])
        if images:
            return images
    return []


def _select_batch_field(messages: list[dict], current_msg: dict, field: str) -> str:
    current = (current_msg.get(field) or "").strip()
    if current:
        return current

    current_user = current_msg.get("user_qq")
    for msg in reversed(messages):
        if msg.get("user_qq") == current_user:
            text = (msg.get(field) or "").strip()
            if text:
                return text

    senders = {msg.get("user_qq") for msg in messages if msg.get("user_qq")}
    if len(senders) == 1:
        for msg in reversed(messages):
            text = (msg.get(field) or "").strip()
            if text:
                return text
    return ""


def _append_unique_limited(items: list[str], value: str, limit: int) -> list[str]:
    if not value:
        return items[-limit:]
    cleaned = [str(item) for item in items if item]
    if value in cleaned:
        return cleaned[-limit:]
    cleaned.append(value)
    return cleaned[-limit:]


def _learn_profile_traits(profile_data: dict | None, text: str) -> dict:
    data = dict(profile_data or {})
    clean = (text or "").strip()
    if not clean:
        return data

    lowered = clean.lower()
    topics = list(data.get("topics") or [])
    style_notes = list(data.get("style_notes") or [])

    for cue, label in PROFILE_TOPIC_KEYWORDS:
        if cue.lower() in lowered:
            topics = _append_unique_limited(topics, label, 10)

    for cues, note in PROFILE_STYLE_CUES:
        if any(cue.lower() in lowered for cue in cues):
            style_notes = _append_unique_limited(style_notes, note, 8)

    if topics:
        data["topics"] = topics
    if style_notes:
        data["style_notes"] = style_notes
    return data


# ─── 消息预处理 ────────────────────────────────────────────────────────────────


def _join_reason_for_action(action: str) -> str:
    return {
        "react": "join_react",
        "short_reply": "join_short",
        "helpful_reply": "join_helpful",
    }.get(action, "join_short")


def _join_probability(action: str, cfg: dict, group_id: str | None = None) -> float:
    probability_cfg = cfg.get("probability", {}) if isinstance(cfg, dict) else {}
    defaults = {
        "react": 0.30,
        "short_reply": 0.58,
        "helpful_reply": 0.82,
    }
    probability = float(probability_cfg.get(action, defaults.get(action, 0.0)))
    if group_id:
        probability *= state.proactive_join_probability_multiplier(group_id)
    return max(0.0, min(0.98, probability))


def _join_candidate_text(text: str, topic: str | None, action: str) -> str:
    topic_hint = f" topic={topic}" if topic else ""
    style = {
        "react": "one light reaction",
        "short_reply": "one short natural reply",
        "helpful_reply": "one useful but concise reply",
    }.get(action, "one short natural reply")
    return f"Proactive join candidate:{topic_hint}; style={style}; trigger={text[:160]}"


def _format_join_instruction(decision_payload: dict) -> str:
    action = decision_payload.get("action", "short_reply")
    max_chars = int(decision_payload.get("max_chars") or 160)
    action_hint = {
        "react": "This should feel like a small in-the-moment reaction, not an answer.",
        "short_reply": "Add one compact conversational turn.",
        "helpful_reply": "Offer useful help, but keep it concise and situated.",
    }.get(action, "Add one compact conversational turn.")
    return (
        "[PROACTIVE_JOIN]\n"
        "This message was not an explicit mention. Only join if it feels natural in the group flow.\n"
        f"action: {action}\n"
        f"score: {decision_payload.get('score', 0)}\n"
        f"reason: {decision_payload.get('reason', '')}\n"
        f"max_chars: {max_chars}\n"
        f"{action_hint}\n"
        "Reply like a restrained group member: no lecture, no greeting, no self-introduction, no forced joke.\n"
        "First decide whether joining still fits the recent group flow. If it does not, output exactly [SILENT] and nothing else.\n"
        "If a human already answered but a short addition still fits, keep it very short.\n"
        "[/PROACTIVE_JOIN]"
    )


def _format_ambient_context_block(ambient_context: str) -> str:
    text = (ambient_context or "").strip()
    if not text:
        return ""
    return (
        "[RECENT_GROUP_FLOW]\n"
        "These are the latest group messages before the current turn. "
        "Use them as live context and keep speaker identities separate.\n"
        f"{text}\n"
        "[/RECENT_GROUP_FLOW]"
    )


def _reply_budget_for_message(default_max: int, frame_max: int, current_msg: dict) -> int:
    budget = min(default_max, frame_max or default_max)
    try:
        join_max = int(current_msg.get("join_max_chars") or 0)
    except (TypeError, ValueError):
        join_max = 0
    if join_max > 0:
        budget = min(budget, join_max)
    return budget


def _is_silent_join_reply(reply: str) -> bool:
    normalized = (reply or "").strip().strip("`").strip().upper()
    return normalized in {"[SILENT]", "SILENT", "[不回复]", "不回复"}


def _choose_local_light_reaction(
    text: str,
    chooser=random.choice,
    avoid: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    avoided = {(item or "").strip() for item in (avoid or [])}
    for cues, replies in LOCAL_LIGHT_REACTION_RULES:
        if any(cue.lower() in lowered for cue in cues):
            available = [reply for reply in replies if reply not in avoided]
            return str(chooser(available or replies))
    return None


_REPEAT_WAVE_REPLIES = (
    "怎么突然开始复读了",
    "这是在排队接暗号吗",
    "你们怎么全刷上了",
)


def _choose_repeat_wave_reaction(
    *,
    avoid: list[str] | tuple[str, ...] | None = None,
    chooser=random.choice,
) -> str:
    avoided = {(item or "").strip() for item in (avoid or [])}
    available = [reply for reply in _REPEAT_WAVE_REPLIES if reply not in avoided]
    return str(chooser(available or list(_REPEAT_WAVE_REPLIES)))


def _recent_assistant_replies(
    group_id: str,
    structured_history: list[dict] | None,
    *,
    limit: int = 6,
) -> list[str]:
    candidates = [
        str(item.get("content") or "").strip()
        for item in (structured_history or [])
        if item.get("role") == "assistant" and str(item.get("content") or "").strip()
    ]
    candidates.extend(state.get_recent_bot_texts(group_id, limit=limit))
    unique: list[str] = []
    for text in candidates:
        if text and (not unique or unique[-1] != text):
            unique.append(text)
    return unique[-max(1, int(limit)):]


def _response_temperature(scene: str, *, proactive: bool) -> float:
    if scene in {"technical_help", "live_info", "weather", "image_question"}:
        return 0.55
    if scene == "support":
        return 0.68
    if scene == "social_ack":
        return 0.72
    return 0.82 if proactive else 0.88


def _context_token_budget(
    default_max_tokens: int,
    scene: str,
    *,
    configured_limits: dict | None = None,
) -> int:
    defaults = {
        "social_ack": 2400,
        "casual_banter": 3600,
        "casual_question": 4000,
        "personal_share": 4200,
        "group_flow": 4600,
        "image_question": 4600,
        "support": 5200,
    }
    limits = configured_limits or {}
    try:
        scene_limit = int(limits.get(scene, defaults.get(scene, default_max_tokens)))
    except (TypeError, ValueError):
        scene_limit = defaults.get(scene, default_max_tokens)
    return max(1200, min(int(default_max_tokens), scene_limit))


def _generation_token_budget(
    reply_max_chars: int,
    *,
    solicited: bool = False,
) -> int:
    """Leave MiMo enough reasoning room even when the visible reply is tiny."""
    visible_chars = max(1, int(reply_max_chars))
    if solicited:
        return max(768, min(1600, visible_chars * 3))
    return max(320, min(1200, visible_chars * 2))


_TURN_CORRECTION_RE = re.compile(
    r"谁问你了|又没问你|没问你|没有问你|没叫你|没有叫你|"
    r"不是问你|没和你说|别乱接话|不要乱接话|怎么.{0,6}乱接话"
)


def _local_turn_correction_reply(text: str) -> str | None:
    if _TURN_CORRECTION_RE.search((text or "").strip()):
        return "确实接错话了，抱歉。"
    return None


def _generation_failure_reply(text: str) -> str:
    clean = (text or "").strip()
    if any(cue in clean for cue in ("?", "？", "怎么", "为什么", "谁", "哪", "吗")):
        return "这题小源刚才没答出来，先不乱编。"
    return "小源刚才卡住了，没接好这句。"


def _typing_delay_seconds(
    reply: str,
    *,
    explicit_trigger: bool,
    proactive: bool,
    action: str = "",
    cfg: dict | None = None,
    jitter: float | None = None,
) -> float:
    timing_cfg = cfg if cfg is not None else get_config().get("human_timing", {})
    if timing_cfg.get("enabled", True) is False:
        return 0.0
    text = (reply or "").strip()
    if not text:
        return 0.0

    cps = max(1.0, float(timing_cfg.get("chars_per_second", 22)))
    min_seconds = max(0.0, float(timing_cfg.get("min_seconds", 0.15)))
    max_seconds = max(min_seconds, float(timing_cfg.get("max_seconds", 3.0)))
    jitter_max = max(0.0, float(timing_cfg.get("jitter_seconds", 0.45)))
    jitter_value = random.uniform(0.0, jitter_max) if jitter is None else max(0.0, jitter)

    delay = len(text) / cps + jitter_value
    delay = max(min_seconds, min(delay, max_seconds))
    if explicit_trigger:
        delay = min(delay, float(timing_cfg.get("explicit_max_seconds", 0.8)))
    if proactive:
        delay = min(delay, float(timing_cfg.get("proactive_max_seconds", 1.2)))
    if action == "react":
        delay = min(delay, 0.8)
    return round(max(0.0, delay), 3)


def _post_send_context_check(
    group_id: str,
    current_msg: dict,
    *,
    explicit_trigger: bool,
    now: float | None = None,
) -> tuple[bool, str]:
    source_message_id = str(current_msg.get("source_message_id") or "")
    if state.is_group_message_recalled(group_id, source_message_id, now=now):
        return False, "source message was recalled"
    if current_msg.get("dialogue_followup"):
        return _post_dialogue_context_check(group_id, current_msg, now=now)
    if explicit_trigger:
        return _post_explicit_context_check(group_id, current_msg, now=now)
    if not current_msg.get("join_instruction"):
        return True, "normal"

    cfg = get_effective_proactive_join_config(
        group_id,
        config=get_config(),
    ).get("post_check", {})
    if cfg.get("enabled", True) is False:
        return True, "disabled"

    if now is None:
        now = _time.time()
    try:
        started_at = float(current_msg.get("timestamp") or now)
    except (TypeError, ValueError):
        started_at = now

    stale_seconds = float(cfg.get("stale_seconds", 30))
    if now - started_at > stale_seconds:
        return False, "stale"

    if cfg.get("cancel_if_bot_spoke", True):
        bot_after = [
            t for t in state.bot_reply_times.get(group_id, [])
            if float(t) > started_at
        ]
        if bot_after:
            return False, "bot already spoke"

    new_human_texts = []
    for item in state.group_recent_texts.get(group_id, []):
        try:
            msg_time = float(item.get("time", 0))
        except (TypeError, ValueError):
            msg_time = 0.0
        if msg_time <= started_at:
            continue
        if str(item.get("user_qq") or "") == str(state.bot_qq_id or ""):
            continue
        new_human_texts.append(str(item.get("text") or ""))

    bot_names = _bot_name_candidates(state.bot_qq_id)
    if any(any(name and name in text for name in bot_names) for text in new_human_texts):
        return False, "new direct mention"

    cancel_after = int(cfg.get("cancel_after_human_messages", 3))
    if len(new_human_texts) >= cancel_after:
        return False, "humans continued"

    return True, "still relevant"


def _post_explicit_context_check(
    group_id: str,
    current_msg: dict,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Let a newer message from the same speaker replace an in-flight answer."""
    if now is None:
        now = _time.time()
    try:
        started_at = float(current_msg.get("timestamp") or now)
    except (TypeError, ValueError):
        started_at = now
    owner = str(current_msg.get("user_qq") or "")
    current_source = str(current_msg.get("source_message_id") or "")
    for item in state.group_recent_texts.get(group_id, []):
        try:
            item_time = float(item.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if item_time <= started_at:
            continue
        if current_source and str(item.get("source_message_id") or "") == current_source:
            continue
        if owner and str(item.get("user_qq") or "") == owner:
            return False, "speaker added a newer turn"
    return True, "explicit turn still current"


def _post_dialogue_context_check(
    group_id: str,
    current_msg: dict,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Avoid sending a generated follow-up after the live group flow moved on."""

    cfg = get_config().get("conversation_followup", {}).get("post_check", {})
    if cfg.get("enabled", True) is False:
        return True, "disabled"
    if now is None:
        now = _time.time()
    try:
        started_at = float(current_msg.get("timestamp") or now)
    except (TypeError, ValueError):
        started_at = now

    stale_seconds = max(1.0, float(cfg.get("stale_seconds", 45)))
    if now - started_at > stale_seconds:
        return False, "dialogue reply became stale"

    owner = str(current_msg.get("user_qq") or "")
    session = state.get_dialogue_session(group_id, now=now)
    if not session or str(session.get("user_qq") or "") != owner:
        return False, "dialogue ownership changed"

    if any(
        float(value) > started_at
        for value in state.bot_reply_times.get(group_id, [])
    ):
        return False, "bot already spoke"

    newer = []
    current_source = str(current_msg.get("source_message_id") or "")
    for item in state.group_recent_texts.get(group_id, []):
        try:
            item_time = float(item.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if item_time <= started_at:
            continue
        if current_source and str(item.get("source_message_id") or "") == current_source:
            continue
        if str(item.get("user_qq") or "") == str(state.bot_qq_id or ""):
            continue
        newer.append(item)

    if any(str(item.get("user_qq") or "") == owner for item in newer):
        return False, "speaker added another message"
    cancel_after = max(1, int(cfg.get("cancel_after_human_messages", 2)))
    if len(newer) >= cancel_after:
        return False, "group flow continued"
    return True, "dialogue still current"


async def _wait_for_natural_send_timing(
    reply: str,
    *,
    group_id: str,
    current_msg: dict,
    explicit_trigger: bool,
    proactive: bool,
) -> bool:
    delay = _typing_delay_seconds(
        reply,
        explicit_trigger=explicit_trigger,
        proactive=proactive,
        action=str(current_msg.get("join_action") or ""),
    )
    if delay > 0:
        await asyncio.sleep(delay)
    ok, reason = _post_send_context_check(
        group_id,
        current_msg,
        explicit_trigger=explicit_trigger,
    )
    if not ok:
        logger.info(
            "Contextual reply cancelled before send: group=%s reason=%s",
            group_id, reason,
        )
        if current_msg.get("join_instruction"):
            _trace_proactive("cancelled_before_send", group_id, reason=reason)
    return ok


def _extract_text(event: MessageEvent) -> str:
    text = event.get_plaintext().strip()
    text = re.sub(r"\[CQ:[^\]]+\]", "", text).strip()
    return text


def _extract_images(event: GroupMessageEvent) -> list[dict]:
    """从 CQ 消息段中提取图片信息，返回 [{"url": ..., "file": ...}] 列表"""
    images = []
    for seg in event.message:
        if seg.type == "image":
            raw_url = seg.data.get("url", "") or ""
            raw_urls = seg.data.get("urls", "")
            if isinstance(raw_urls, list) and raw_urls:
                raw_url = raw_urls[0]
            elif isinstance(raw_urls, str) and raw_urls.strip():
                raw_url = raw_urls.strip()
            url = (raw_url or "").strip()
            file_id = seg.data.get("file", "")
            logger.info("[IMAGE] url='%s' file=%s media_=%s",
                        url[:120] if url else "(empty)", file_id,
                        str(seg.data.get("media_", ""))[:200])
            images.append({"url": url, "file": file_id})
    if images:
        logger.info("[IMAGE] 提取到 %d 张图片", len(images))
    return images


async def _recognize_images(
    image_infos: list[dict],
    bot,
) -> tuple[list[str], str | None, str | None]:
    """识别图片：先 URL 下载，失败则用 OneBot WS call_api

    Returns:
        (descriptions, first_image_url, first_image_desc)
    """
    from .vision import describe_image_from_url, describe_image_from_bytes

    max_images = max(
        1,
        int(get_config().get("vision", {}).get("max_images_per_turn", 2)),
    )
    selected = image_infos[:max_images]
    if len(image_infos) > max_images:
        logger.info(
            "[IMAGE] 本轮收到 %d 张图，只识别前 %d 张",
            len(image_infos),
            max_images,
        )

    async def recognize_one(info: dict) -> tuple[str | None, str]:
        url = str(info.get("url") or "")
        file_id = str(info.get("file") or "")
        cached_description = str(info.get("description") or "").strip()
        if cached_description:
            logger.info("[IMAGE] 复用当前 QQ 最近图片的识别结果")
            return cached_description, url or file_id
        result = None

        if url:
            logger.info("[IMAGE] URL 下载: %s", url[:120])
            result = await describe_image_from_url(url)
            if result and not result.startswith("[图片"):
                logger.info("[IMAGE] URL 识别成功")
            else:
                logger.warning("[IMAGE] URL 识别失败: %s", result)
                result = None

        if result is None and file_id:
            logger.info("[IMAGE] OneBot WS get_image: file=%s", file_id)
            try:
                api_resp = await bot.call_api("get_image", file=file_id)
                logger.info("[IMAGE] get_image resp type=%s", type(api_resp).__name__)
                image_bytes = await _parse_get_image_response(api_resp)
                if image_bytes and len(image_bytes) > 100:
                    result = await describe_image_from_bytes(image_bytes)
                    if result and not result.startswith("[图片"):
                        logger.info(
                            "[IMAGE] WS API 识别成功, %d bytes",
                            len(image_bytes),
                        )
                    else:
                        logger.warning("[IMAGE] WS API 识别返回: %s", result)
                        result = None
                else:
                    logger.warning(
                        "[IMAGE] WS get_image 空数据, len=%d",
                        len(image_bytes) if image_bytes else 0,
                    )
            except Exception as exc:
                logger.exception("[IMAGE] WS get_image 异常: %s", exc)

        if result and not result.startswith("[图片"):
            return result, url or file_id
        logger.warning("[IMAGE] 跳过（识别失败）: url=%s file=%s", url, file_id)
        return None, url or file_id

    results = await asyncio.gather(*(recognize_one(info) for info in selected))
    descriptions = [result for result, _ in results if result]
    first_result = next(
        ((result, source) for result, source in results if result),
        (None, None),
    )
    return (
        [f"[图片内容描述：{result}]" for result in descriptions],
        first_result[1],
        first_result[0],
    )


async def _parse_get_image_response(resp) -> bytes | None:
    """解析 OneBot v11 get_image 响应，返回图片字节数据"""
    if isinstance(resp, bytes):
        return resp
    if isinstance(resp, dict):
        file_val = resp.get("file", "") or resp.get("data", "")
        if isinstance(file_val, str):
            if file_val.startswith("base64://"):
                import base64
                return base64.b64decode(file_val[len("base64://"):])
            # 本地文件路径
            path = Path(file_val)
            if path.exists():
                return path.read_bytes()
            logger.warning("[IMAGE] get_image 返回的文件路径不存在: %s", file_val)
        elif isinstance(file_val, bytes):
            return file_val
    return None


def _is_mentioned(event: GroupMessageEvent, bot_qq: str) -> bool:
    for seg in event.message:
        if seg.type == "at" and seg.data.get("qq") == bot_qq:
            return True
    raw = str(event.message)
    if f"@{bot_qq}" in raw or f"qq={bot_qq}" in raw:
        return True
    return False


def _is_called(text: str) -> bool:
    if not text:
        return False
    for name in _bot_name_candidates(include_at_aliases=False):
        if name in text:
            return True
    return False


def _bot_name_candidates(
    bot_qq: str | None = None,
    *,
    include_at_aliases: bool = True,
) -> list[str]:
    names = []
    if bot_qq:
        names.append(str(bot_qq))
    bot_cfg = get_config().get("bot", {})
    configured = bot_cfg.get("nickname", "小源")
    if isinstance(configured, str):
        names.append(configured)
    else:
        names.extend(str(n) for n in configured if n)
    if include_at_aliases:
        at_aliases = bot_cfg.get("at_aliases", [])
        if isinstance(at_aliases, str):
            names.append(at_aliases)
        else:
            names.extend(str(n) for n in at_aliases if n)
    names.extend(["小源"])
    return [n for i, n in enumerate(names) if n and n not in names[:i]]


def _is_text_at_mention(event: GroupMessageEvent, bot_qq: str | None = None) -> bool:
    """LLBot sends @ as plain text, not CQ at segment."""
    candidates = _bot_name_candidates(bot_qq)
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "").strip()
            if any(f"@{name}" in t for name in candidates):
                return True
    return False


def _extract_at_text(event: GroupMessageEvent, bot_qq: str) -> str:
    parts: list[str] = []
    bot_names = sorted(_bot_name_candidates(bot_qq), key=len, reverse=True)
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "")
            for name in bot_names:
                t = t.replace(f"@{name}", "")
            if t:
                parts.append(t)
        elif seg.type == "at":
            qq = str(seg.data.get("qq", ""))
            if qq != str(bot_qq):
                display = seg.data.get("name") or qq or "unknown"
                parts.append(f"@{display}")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _extract_mentioned_qqs(event: GroupMessageEvent) -> list[str]:
    return [
        str(seg.data.get("qq"))
        for seg in event.message
        if seg.type == "at" and seg.data.get("qq") is not None
    ]


def _has_text_at_other_member(event: GroupMessageEvent, bot_qq: str) -> bool:
    bot_names = set(_bot_name_candidates(bot_qq))
    for seg in event.message:
        if seg.type != "text":
            continue
        text = str(seg.data.get("text") or "")
        for match in re.finditer(r"(?:^|\s)@([^\s，,：:]{1,40})", text):
            name = match.group(1).strip()
            if name and name not in bot_names and name not in {"全体成员", "all"}:
                return True
    return False


def _is_addressed_elsewhere(
    *,
    mentioned_qqs: list[str] | None,
    bot_qq: str | None,
    direct_bot_address: bool,
    reply_to_message_id: str | None,
    quoted_user_qq: str | None,
    text_at_other_member: bool = False,
) -> bool:
    """Recognize a human-to-human turn before any proactive reply path."""

    if direct_bot_address:
        return False
    bot_id = str(bot_qq or "")
    if any(str(qq) and str(qq) != bot_id for qq in (mentioned_qqs or [])):
        return True
    if text_at_other_member:
        return True
    if reply_to_message_id:
        return not quoted_user_qq or str(quoted_user_qq) != bot_id
    return False


def _extract_reply_message_id(event: GroupMessageEvent) -> str | None:
    for seg in event.message:
        if seg.type == "reply":
            value = seg.data.get("id") or seg.data.get("message_id")
            if value is not None:
                return str(value)
    return None


def _event_source_message_id(event: MessageEvent) -> str:
    value = getattr(event, "message_id", None)
    if value is not None:
        return str(value)
    return f"ephemeral:{event.user_id}:{_time.time_ns()}"


def _resolve_message_text(
    *,
    group_id: str,
    user_qq: str,
    source_message_id: str,
    text: str,
    at_text: str,
    should_respond: bool,
    now: float,
) -> tuple[str, bool]:
    """Resolve a split turn where a standalone @bot submits prior text."""

    message_text = (at_text or text or "").strip()
    carried_from_previous = False
    if should_respond and not at_text:
        previous = state.find_recent_text_by_user(
            group_id,
            user_qq=user_qq,
            exclude_source_message_id=source_message_id,
            now=now,
        )
        if previous is not None:
            message_text = str(previous.get("text") or "").strip()
            carried_from_previous = bool(message_text)
    if not message_text:
        message_text = "[有成员@了小源]" if should_respond else "[非文本群消息]"
    return message_text, carried_from_previous


def _message_payload_text(payload) -> str:
    if isinstance(payload, str):
        return re.sub(r"\[CQ:[^\]]+\]", "", payload).strip()
    if not isinstance(payload, list):
        return ""
    parts = []
    for segment in payload:
        if not isinstance(segment, dict):
            continue
        data = segment.get("data") or {}
        if segment.get("type") == "text":
            parts.append(str(data.get("text") or ""))
        elif segment.get("type") == "at":
            parts.append(f"@{data.get('name') or data.get('qq') or '成员'}")
    return " ".join(part.strip() for part in parts if part.strip()).strip()


async def _resolve_reply_context(
    bot: Bot,
    *,
    group_id: str,
    reply_to_message_id: str | None,
) -> tuple[str, str | None]:
    if not reply_to_message_id:
        return "", None

    async with await get_session() as session:
        quoted = await get_message_by_source_id(
            session,
            group_id=group_id,
            source_message_id=reply_to_message_id,
        )
        if quoted is not None:
            quoted_is_bot = quoted.role == "assistant"
            quoted_qq = (
                str(state.bot_qq_id or get_config().get("bot", {}).get("qq_id") or "")
                if quoted_is_bot
                else str(quoted.user_qq or "")
            )
            names = await get_user_display_names(
                session,
                [quoted.user_qq] if quoted.user_qq else [],
            )
            speaker = (
                str(get_config().get("bot", {}).get("nickname") or "小源")
                if quoted_is_bot
                else names.get(quoted_qq) or (f"QQ{quoted_qq}" if quoted_qq else "成员")
            )
            return (
                f"[回复 {speaker} (QQ:{quoted_qq or 'bot'})]: "
                f"{quoted.content[:300]}",
                quoted_qq or None,
            )

    try:
        response = await asyncio.wait_for(
            bot.call_api("get_msg", message_id=int(reply_to_message_id)),
            timeout=2.5,
        )
    except Exception:
        logger.info(
            "Unable to resolve reply target: group=%s message=%s",
            group_id,
            reply_to_message_id,
        )
        return "", None

    if not isinstance(response, dict):
        return "", None
    sender = response.get("sender") or {}
    quoted_qq = sender.get("user_id") or response.get("user_id")
    speaker = sender.get("card") or sender.get("nickname") or (
        f"QQ{quoted_qq}" if quoted_qq else "成员"
    )
    quoted_text = _message_payload_text(response.get("message"))
    if not quoted_text:
        quoted_text = str(response.get("raw_message") or "").strip()
    if not quoted_text:
        return "", str(quoted_qq) if quoted_qq else None
    return (
        f"[回复 {speaker} (QQ:{quoted_qq or 'unknown'})]: {quoted_text[:300]}",
        str(quoted_qq) if quoted_qq else None,
    )


async def _update_user_profile(
    qq_id: str,
    nickname: str | None = None,
    *,
    text: str = "",
):
    async with await get_session() as session:
        result = await session.execute(select(User).where(User.qq_id == qq_id))
        user = result.scalar_one_or_none()
        changed = user is None
        if user is None:
            user = User(qq_id=qq_id)
            session.add(user)
        if nickname and user.nickname != nickname:
            user.nickname = nickname
            changed = True
        current_profile = user.get_profile()
        learned = _learn_profile_traits(current_profile, text)
        if learned != current_profile:
            user.set_profile(learned)
            changed = True
        if changed:
            await session.commit()


# ─── 回复发送 ──────────────────────────────────────────────────────────────────


async def _send_group_reply(bot: Bot, group_id: str, content: str):
    if not content or not content.strip():
        logger.warning("_send_group_reply called with empty content, skipping")
        return
    try:
        print(f"[REPLY] group={group_id} len={len(content)}: {content[:200]}")
    except Exception:
        pass
    return await send_group_text(bot, group_id, content)


def _record_dialogue_delivery(
    *,
    group_id: str,
    user_qq: str | None,
    text: str,
    source_message_id: str | None,
    current_msg: dict,
) -> None:
    """Renew a delivered solicited turn without reclaiming newer ownership."""

    if not user_qq:
        return
    dialogue_cfg = get_config().get("conversation_followup", {})
    ttl_seconds = float(dialogue_cfg.get("timeout_seconds", 240))
    owner = str(user_qq)
    marked = state.mark_dialogue_bot_reply(
        group_id,
        user_qq=owner,
        text=text,
        source_message_id=source_message_id,
        ttl_seconds=ttl_seconds,
    )
    if not marked:
        active_dialogue = state.get_dialogue_session(group_id)
        if not active_dialogue:
            state.start_dialogue_session(
                group_id,
                user_qq=owner,
                source_message_id=current_msg.get("source_message_id"),
                origin="reply",
                ttl_seconds=ttl_seconds,
            )
            state.mark_dialogue_bot_reply(
                group_id,
                user_qq=owner,
                text=text,
                source_message_id=source_message_id,
                ttl_seconds=ttl_seconds,
            )
        else:
            logger.info(
                "Dialogue reply did not reclaim newer ownership: group=%s replied_user=%s active_user=%s",
                group_id,
                owner,
                active_dialogue.get("user_qq"),
            )
    if current_msg.get("dialogue_close_after_reply"):
        state.close_dialogue_session(group_id, user_qq=owner)
    from .runtime_state import schedule_persist

    schedule_persist()


# ─── 核心处理逻辑 ──────────────────────────────────────────────────────────────


async def _process_messages(key: str, messages: list[dict]):
    """静默窗口到期后，处理一批消息并生成回复"""
    started_at = _time.perf_counter()
    if not messages:
        return

    messages = [
        message
        for message in messages
        if not state.is_group_message_recalled(
            str(message.get("group_id") or ""),
            str(message.get("source_message_id") or ""),
        )
    ]
    if not messages:
        logger.info("Reply batch dropped because all source messages were recalled")
        return

    try:
        bot = get_bot()
    except Exception:
        logger.error("Failed to get bot instance")
        return

    first_msg = messages[0]
    group_id = first_msg.get("group_id")
    if not group_id:
        return

    # 安全网：整个处理流程包在 try 里，意外出错也发个 fallback 不装死
    try:
        await _process_messages_inner(bot, group_id, first_msg, messages)
    except Exception:
        logger.exception("_process_messages unexpected error for group %s", group_id)
        has_explicit = any(
            bool(message.get("explicit_trigger")) for message in messages
        )
        context_only_gate = any(
            bool(message.get("dialogue_followup") or message.get("join_instruction"))
            for message in messages
        )
        if context_only_gate and not has_explicit:
            return
        try:
            fallback = "小源刚才处理这句时出错了，先不乱回。"
            sent_message_id = await _send_group_reply(
                bot, group_id,
                fallback,
            )
            if has_explicit:
                current_msg = _select_current_message(messages) or first_msg
                _record_dialogue_delivery(
                    group_id=group_id,
                    user_qq=current_msg.get("user_qq"),
                    text=fallback,
                    source_message_id=sent_message_id,
                    current_msg=current_msg,
                )
        except Exception:
            pass
    finally:
        logger.info(
            "Reply pipeline finished: group=%s batch=%d explicit=%s elapsed=%.3fs",
            group_id,
            len(messages),
            any(bool(message.get("explicit_trigger")) for message in messages),
            _time.perf_counter() - started_at,
        )


async def _process_messages_inner(
    bot, group_id: str, first_msg: dict, messages: list[dict]
):
    current_msg = _select_current_message(messages) or first_msg
    user_qq = current_msg.get("user_qq")
    mode = current_msg.get("mode", "normal")
    mode_target = current_msg.get("mode_target", "")
    explicit_trigger = any(bool(m.get("explicit_trigger", False)) for m in messages)
    dialogue_followup = bool(current_msg.get("dialogue_followup"))
    solicited_trigger = explicit_trigger or dialogue_followup

    # Fetch user profile and build identity prefix
    profile = {}
    user_display = f"QQ{user_qq}" if user_qq else "未知成员"
    display_names: dict[str, str] = {}
    if user_qq:
        async with await get_session() as session:
            profile = await get_user_profile_summary(session, user_qq)
            display_names = await get_user_display_names(
                session,
                [m.get("user_qq") for m in messages],
            )
        user_display = display_names.get(user_qq) or _display_name_from_profile(profile, user_qq)

    # 夸夸/点草模式：查找目标用户的画像
    target_profile = None
    if mode in ("praise", "roast") and mode_target:
        async with await get_session() as session:
            result = await session.execute(
                select(User).where(User.qq_id == mode_target)
            )
            target_user = result.scalar_one_or_none()
            if target_user:
                target_profile = await get_user_profile_summary(session, target_user.qq_id)
            else:
                # 昵称只用于查找候选；同名关联到多个 QQ 时绝不猜身份。
                result = await session.execute(
                    select(UserNickname.user_qq).where(
                        UserNickname.nickname_id == select(Nickname.id).where(
                            Nickname.name == mode_target
                        ).scalar_subquery(),
                    )
                )
                candidate_qqs = sorted({str(value) for value in result.scalars().all()})
                if len(candidate_qqs) == 1:
                    target_profile = await get_user_profile_summary(
                        session,
                        candidate_qqs[0],
                    )
                elif len(candidate_qqs) > 1:
                    logger.warning(
                        "Ambiguous nickname target ignored: name=%s qqs=%s",
                        mode_target,
                        candidate_qqs,
                    )

    raw_text = (current_msg.get("text") or "").strip()
    trigger_images = _select_batch_images(messages, current_msg)
    batch_context = _format_batch_context(messages, display_names, current_msg)
    ambient_context = (current_msg.get("ambient_context") or "").strip()
    ambient_context_block = _format_ambient_context_block(ambient_context)
    frame_context = batch_context or ambient_context
    planned_search_text = _select_search_text(messages, current_msg)
    planned_weather_query = _select_weather_query(messages, current_msg)
    frame = build_conversation_frame(
        current_msg={**current_msg, "images": trigger_images},
        raw_text=raw_text,
        batch_context=frame_context,
        explicit_trigger=solicited_trigger,
        search_query=planned_search_text,
        weather_query=planned_weather_query,
    )
    frame_instruction = build_frame_instruction(frame)
    join_instruction = (current_msg.get("join_instruction") or "").strip()
    dialogue_instruction = (
        current_msg.get("dialogue_instruction") or ""
    ).strip()
    strategy_text = "\n".join(
        part
        for part in [
            batch_context or ambient_context_block or raw_text,
            frame_instruction,
            join_instruction,
            dialogue_instruction,
        ]
        if part
    )

    # 构建用户消息
    if mode == "praise":
        target_name = target_profile.get("nickname", mode_target) if target_profile else mode_target
        current_text = f"请夸夸 {target_name}。下面是 {target_name} 的信息，请据此写夸赞。"
    elif mode == "roast":
        target_name = target_profile.get("nickname", mode_target) if target_profile else mode_target
        current_text = f"请点草 {target_name}。下面是 {target_name} 的信息，请据此写友好吐槽。"
    elif mode == "joke":
        current_text = "来个冷笑话"
    else:
        current_text = raw_text
    if trigger_images and current_text in {
        "",
        "[非文本群消息]",
        "[有成员@了小源]",
    }:
        current_text = f"[发送了{len(trigger_images)}张图片]"
    combined_text = _format_current_user_message(
        user_display=user_display,
        user_qq=user_qq,
        raw_text=current_text,
        mode=mode,
        batch_context=batch_context,
    )
    if ambient_context_block and not batch_context:
        combined_text = f"{combined_text}\n\n{ambient_context_block}"

    memory_cfg = get_config().get("memory", {})
    context, meta, structured_history = await build_context(
        scene="group",
        user_qq=user_qq,
        group_id=group_id,
        half_life_minutes=memory_cfg.get("weight_half_life_minutes", 60),
        max_tokens=_context_token_budget(
            int(memory_cfg.get("max_context_tokens", 8000)),
            frame.scene,
            configured_limits=memory_cfg.get("scene_context_tokens", {}),
        ),
        current_query=(frame.tool_plan.search_query if frame.tool_plan else "") or raw_text,
        exclude_message_ids=[
            m["message_id"]
            for m in messages
            if m.get("message_id") is not None
        ],
    )


    # 夸夸/点草模式：附上目标用户画像
    llm_profile = profile
    if target_profile and target_profile.get("exists"):
        combined_text += (
            "\n\n[目标成员信息]\n"
            f"QQ: {target_profile.get('qq_id', 'unknown')}（唯一身份主键）\n"
            f"昵称: {target_profile.get('nickname', '未知')}\n"
        )
        combined_text += f"称呼: {', '.join(target_profile.get('nicknames', []))}\n"
        combined_text += f"互动消息数: {target_profile.get('total_messages', 0)}\n"
        pdata = target_profile.get('profile', {})
        if pdata.get('topics'):
            combined_text += f"技术方向: {', '.join(pdata['topics'])}\n"

    llm = get_llm()
    strategy = await decide_reply_strategy(
        llm=llm,
        group_id=group_id,
        raw_text=strategy_text or combined_text,
        context=context,
        user_profile=llm_profile,
        mode=mode,
        explicit_trigger=solicited_trigger,
        proactive=bool(join_instruction),
    )
    if mode != "normal" and not strategy.should_reply:
        strategy = fallback_strategy(
            raw_text=strategy_text or combined_text,
            context=context,
            user_profile=llm_profile,
            mode=mode,
        )
    if not strategy.should_reply:
        logger.info(
            "Humanize strategy skipped reply: group=%s user=%s reason=%s",
            group_id, user_qq, strategy.reason,
        )
        if join_instruction:
            _trace_proactive(
                "declined_by_strategy",
                group_id,
                action=current_msg.get("join_action", ""),
                reason=strategy.reason,
            )
        return

    # ── 工具调用并发执行；图片也在群级回复队列内完成，避免消息入口被卡住 ──
    plan = frame.tool_plan
    weather_query = plan.weather_query if plan and plan.needs_weather else ""
    search_text = plan.search_query if plan and plan.needs_search else ""
    vision_task = None
    if trigger_images:
        vision_total_timeout = float(
            get_config().get("vision", {}).get("total_timeout_seconds", 30)
        )
        logger.info("[IMAGE] 回复队列开始识别 %d 张图片", len(trigger_images))
        vision_task = asyncio.create_task(
            asyncio.wait_for(
                _recognize_images(trigger_images, bot),
                timeout=vision_total_timeout,
            )
        )
    weather_task = asyncio.create_task(query_weather(weather_query)) if weather_query else None
    search_task = asyncio.create_task(run_smart_search_result(search_text)) if search_text else None

    if vision_task:
        desc_parts: list[str] = []
        image_url_for_memory = None
        image_desc_for_memory = None
        try:
            (
                desc_parts,
                image_url_for_memory,
                image_desc_for_memory,
            ) = await vision_task
        except asyncio.TimeoutError:
            logger.warning("[IMAGE] 本轮视觉识别总耗时超限")
        except Exception:
            logger.exception("[IMAGE] 本轮视觉识别异常")

        if desc_parts:
            combined_text = f"{combined_text}\n\n" + "\n".join(desc_parts)
            logger.info("[IMAGE] 图片识别完成，已附加 %d 条描述", len(desc_parts))

            first_info = next(
                (
                    info
                    for info in trigger_images
                    if (str(info.get("url") or info.get("file") or ""))
                    == str(image_url_for_memory or "")
                ),
                trigger_images[0],
            )
            memory_message_id = first_info.get("message_id") or current_msg.get("message_id")
            memory_content = str(
                first_info.get("memory_content")
                or current_msg.get("text")
                or "[用户发送了图片]"
            ).strip()
            if memory_content in {"[非文本群消息]", "[有成员@了小源]"}:
                memory_content = "[用户发送了图片]"
            for desc_part in desc_parts:
                if desc_part not in memory_content:
                    memory_content = f"{memory_content}\n{desc_part}"

            if memory_message_id is not None:
                try:
                    memory_message_id = int(memory_message_id)
                    async with await get_session() as session:
                        await update_message_content(
                            session,
                            memory_message_id,
                            content=memory_content,
                            image_url=image_url_for_memory,
                            image_description=image_desc_for_memory,
                        )
                        await session.commit()
                    schedule_vector_refresh(
                        message_id=memory_message_id,
                        content=memory_content,
                        user_qq=str(first_info.get("user_qq") or user_qq or ""),
                        group_id=group_id,
                        scene="group",
                        created_at=float(
                            first_info.get("timestamp")
                            or current_msg.get("timestamp")
                            or _time.time()
                        ),
                    )
                    for cached_image in state.group_recent_images.get(group_id, []):
                        same_message = (
                            first_info.get("message_id") is not None
                            and cached_image.get("message_id") == first_info.get("message_id")
                        )
                        same_source = (
                            first_info.get("source_message_id")
                            and cached_image.get("source_message_id")
                            == first_info.get("source_message_id")
                        )
                        if (
                            str(cached_image.get("user_qq") or "")
                            == str(first_info.get("user_qq") or user_qq or "")
                            and (same_message or same_source)
                        ):
                            cached_image["description"] = image_desc_for_memory
                except Exception:
                    logger.exception(
                        "[IMAGE] 识别成功但写回消息记忆失败: message=%s",
                        memory_message_id,
                    )
        else:
            combined_text = (
                f"{combined_text}\n\n[视觉工具状态]\n"
                "这次图片识别失败或超时。不要猜测图片内容；自然说明暂时没看清即可。"
            )
            logger.warning("[IMAGE] 所有图片识别均失败")

    if weather_task:
        try:
            weather_data = await weather_task
            if weather_data:
                combined_text = (
                    f"{combined_text}\n\n"
                    "[系统注：以下是实时天气工具返回的数据，请根据天气自然回答，"
                    "不要生硬列出所有字段，也不要改写成别的城市。]\n"
                    f"{weather_data}"
                )
        except Exception:
            logger.exception("[Weather] error for '%s'", weather_query[:60])

    if search_task:
        try:
            search_timeout = float(
                get_config().get("web_search", {}).get("timeout_seconds", 8)
            )
            search_result = await asyncio.wait_for(
                search_task,
                timeout=search_timeout,
            )
            if search_result.context:
                combined_text = (
                    f"{combined_text}\n\n"
                    f"[系统指令：以下是实时联网搜索结果。"
                    f"你必须以搜索结果为准来回答，你的训练数据已过时不可信。"
                    f"用自然群聊口吻转述搜索结果即可。"
                    f"如果搜索结果与用户问题明显不相关或全是无关内容，"
                    f"诚实告诉用户「没搜到相关内容，换个关键词试试」。"
                    f"不要假装有搜索结果，不要自己编。]\n{search_result.context}"
                )
                print(f"[Search] Results appended for group {group_id} ({len(search_result.context)} chars)")
            elif search_result.required or (plan and plan.search_required):
                combined_text = (
                    f"{combined_text}\n\n"
                    "[联网工具状态]\n"
                    f"用户这次问题需要实时搜索，但搜索工具没有返回可用结果。"
                    f"状态: {search_result.status}; 原因: {search_result.reason or '未知'}; "
                    f"查询: {search_result.query or search_text}\n"
                    "回答时必须诚实说明无法确认最新信息，不要编造搜索结果。"
                )
        except asyncio.TimeoutError:
            logger.warning("[Search] timed out for '%s'", search_text[:60])
            combined_text = (
                f"{combined_text}\n\n[联网工具状态]\n"
                "实时搜索超时，回答时说明暂时无法确认最新信息，不要编造。"
            )
        except Exception:
            logger.exception("[Search] error for '%s'", search_text[:60])

    max_chars = get_config().get("output", {}).get("max_chars_per_message", 800)
    reply_max_chars = _reply_budget_for_message(max_chars, frame.max_chars, current_msg)
    tone_instruction = build_tone_instruction(
        scene=frame.scene,
        style=strategy.reply_style,
        explicit_trigger=frame.explicit_trigger,
        proactive=bool(join_instruction),
        max_chars=reply_max_chars,
    )
    recent_assistant_replies = _recent_assistant_replies(
        group_id,
        structured_history,
    )
    adaptive_style_instruction = build_adaptive_style_instruction(
        recent_group_messages=state.group_recent_texts.get(group_id, []),
        recent_assistant_replies=recent_assistant_replies,
        current_text=raw_text,
        speaker_name=user_display,
        scene=frame.scene,
    )
    dynamic_system_prompt = "\n\n".join(
        part
        for part in (
            build_system_prompt(
                scene="group",
                user_profile=llm_profile,
                mode=mode,
                group_id=group_id,
            ),
            frame_instruction,
            join_instruction,
            dialogue_instruction,
            build_humanize_instruction(strategy),
            tone_instruction,
            adaptive_style_instruction,
        )
        if part
    )

    if strategy.delay_seconds > 0:
        await asyncio.sleep(strategy.delay_seconds)

    # 群级并发锁：同一时间只处理一个 LLM 请求
    llm_lock: asyncio.Lock = state.get_llm_lock(group_id)
    reply = ""
    try:
        async with asyncio.timeout(45):
            async with llm_lock:
                print(f"[LLM] Starting chat: mode={mode} group={group_id}")
                try:
                    reply = await llm.chat(
                        context=context,
                        user_profile=llm_profile,
                        scene="group",
                        user_message=combined_text,
                        mode=mode,
                        structured_history=structured_history,
                        group_id=group_id,
                        max_tokens=_generation_token_budget(
                            reply_max_chars,
                            solicited=solicited_trigger,
                        ),
                        temperature=_response_temperature(
                            frame.scene,
                            proactive=bool(join_instruction),
                        ),
                        system_prompt=dynamic_system_prompt,
                    )
                    if not (reply or "").strip():
                        if join_instruction or dialogue_instruction:
                            logger.info(
                                "Context-gated reply abandoned after empty LLM output: group=%s kind=%s",
                                group_id,
                                "dialogue" if dialogue_instruction else "proactive",
                            )
                            if join_instruction:
                                _trace_proactive("empty_generation", group_id)
                            return
                        reply = _generation_failure_reply(raw_text)
                    if (
                        join_instruction or dialogue_instruction
                    ) and _is_silent_join_reply(reply):
                        if dialogue_instruction:
                            state.close_dialogue_session(
                                group_id,
                                user_qq=str(user_qq or ""),
                            )
                            from .runtime_state import schedule_persist

                            schedule_persist()
                            logger.info(
                                "Dialogue continuation declined by generation AI: group=%s user=%s reason=%s",
                                group_id,
                                user_qq,
                                current_msg.get("dialogue_reason", ""),
                            )
                        else:
                            logger.info(
                                "Proactive join declined by generation AI: group=%s action=%s",
                                group_id, current_msg.get("join_action", ""),
                            )
                            _trace_proactive(
                                "declined_by_generation",
                                group_id,
                                action=current_msg.get("join_action", ""),
                            )
                        return
                    reply = post_check_reply(
                        reply,
                        frame=frame,
                        style=strategy.reply_style,
                        default_max_chars=max_chars,
                    )
                    reply = shape_reply(
                        strategy,
                        reply,
                        default_max_chars=reply_max_chars,
                    )
                    reply = polish_tone(
                        reply,
                        scene=frame.scene,
                        style=strategy.reply_style,
                        explicit_trigger=frame.explicit_trigger,
                        proactive=bool(join_instruction),
                        max_chars=reply_max_chars,
                        recent_assistant_replies=recent_assistant_replies,
                        speaker_name=user_display,
                        current_text=raw_text,
                    )
                    reply = apply_outgoing_group_policy(
                        reply,
                        group_id,
                        recent_bot_texts=state.get_recent_bot_texts(
                            group_id,
                            limit=20,
                        ),
                    )
                    print(f"[LLM] Reply received: len={len(reply)}")
                    # 情绪追踪：保持跨轮次角色连贯
                    if group_id:
                        mood_result = _detect_mood(reply)
                        if mood_result:
                            from .state import update_group_mood
                            update_group_mood(group_id, mood_result[0], mood_result[1])
                except Exception as e:
                    logger.exception("LLM call failed")
                    if join_instruction or dialogue_instruction:
                        logger.info(
                            "Context-gated reply abandoned after LLM failure: group=%s kind=%s",
                            group_id,
                            "dialogue" if dialogue_instruction else "proactive",
                        )
                        if join_instruction:
                            _trace_proactive("llm_failure", group_id)
                        return
                    reply = _generation_failure_reply(raw_text)
                    print(f"[LLM] Exception: {e}")
    except TimeoutError:
        logger.warning("LLM lock timeout for group %s", group_id)
        if join_instruction or dialogue_instruction:
            logger.info(
                "Context-gated reply abandoned after LLM timeout: group=%s kind=%s",
                group_id,
                "dialogue" if dialogue_instruction else "proactive",
            )
            if join_instruction:
                _trace_proactive("llm_timeout", group_id)
            return
        print(f"[LLM] Lock timeout for group={group_id}, sending fallback")
        try:
            fallback = _generation_failure_reply(raw_text)
            sent_message_id = await _send_group_reply(
                bot, group_id,
                fallback,
            )
            _record_dialogue_delivery(
                group_id=group_id,
                user_qq=user_qq,
                text=fallback,
                source_message_id=sent_message_id,
                current_msg=current_msg,
            )
        except Exception:
            pass
        return

    if not await _wait_for_natural_send_timing(
        reply,
        group_id=group_id,
        current_msg=current_msg,
        explicit_trigger=solicited_trigger,
        proactive=bool(join_instruction),
    ):
        return

    try:
        sent_message_id = await _send_group_reply(bot, group_id, reply)
    except Exception:
        logger.exception("Reply delivery failed: group=%s", group_id)
        return

    try:
        if join_instruction:
            state.mark_proactive_join_sent(
                group_id,
                source_message_id=sent_message_id,
            )
            _trace_proactive(
                "sent",
                group_id,
                action=current_msg.get("join_action", ""),
                chars=len(reply),
            )
        if solicited_trigger and not join_instruction and user_qq:
            _record_dialogue_delivery(
                group_id=group_id,
                user_qq=str(user_qq),
                text=reply,
                source_message_id=sent_message_id,
                current_msg=current_msg,
            )
        logger.info("Reply sent: group=%s chars=%d", group_id, len(reply))
    except Exception:
        logger.exception(
            "Reply delivered but post-send state update failed: group=%s",
            group_id,
        )

    threshold = get_config().get("memory", {}).get("compress_threshold_tokens", 15000)
    schedule_memory_compression("group", user_qq, group_id, threshold)


# ─── 消息接收 ──────────────────────────────────────────────────────────────────


async def _on_message(event: MessageEvent):
    await _ensure_init()

    bot = get_bot()
    if state.bot_qq_id is None:
        state.bot_qq_id = str(bot.self_id)

    text = _extract_text(event)
    user_qq = str(event.user_id)
    sender = event.sender
    nickname = (
        (getattr(sender, "card", None) or getattr(sender, "nickname", None))
        if sender
        else None
    )

    # Group-only bot: do not create profiles or memories from private messages.
    if isinstance(event, PrivateMessageEvent):
        return

    # 群聊处理
    if isinstance(event, GroupMessageEvent):
        group_id_str = str(event.group_id)

        if not _is_allowed_group(group_id_str):
            return

        now_ts = _time.time()
        source_message_id = _event_source_message_id(event)
        mentioned_qqs = _extract_mentioned_qqs(event)
        text_at_other_member = _has_text_at_other_member(event, state.bot_qq_id)
        reply_to_message_id = _extract_reply_message_id(event)
        mentions_bot = _is_mentioned(event, state.bot_qq_id)
        called_bot = _is_called(text)
        at_text = _extract_at_text(event, state.bot_qq_id)
        text_at = _is_text_at_mention(event, state.bot_qq_id)
        to_me = getattr(event, "to_me", False)
        explicit_trigger = bool(mentions_bot or called_bot or text_at or to_me)

        message_text, carried_from_previous = _resolve_message_text(
            group_id=group_id_str,
            user_qq=user_qq,
            source_message_id=source_message_id,
            text=text,
            at_text=at_text,
            should_respond=explicit_trigger,
            now=now_ts,
        )
        other_mentions = [
            qq for qq in mentioned_qqs if qq != str(state.bot_qq_id)
        ]
        if other_mentions:
            async with await get_session() as session:
                mention_names = await get_user_display_names(session, other_mentions)
            for mentioned_qq, display_name in mention_names.items():
                message_text = message_text.replace(
                    f"@{mentioned_qq}",
                    f"@{display_name}",
                )

        message_id, created = await store_inbound_memory(
            group_id=group_id_str,
            source_message_id=source_message_id,
            user_qq=user_qq,
            nickname=nickname,
            content=message_text,
            profile_learner=_learn_profile_traits,
        )
        if not created:
            logger.info(
                "Ignored replayed group event: group=%s message=%s",
                group_id_str,
                source_message_id,
            )
            return

        quoted_context, quoted_user_qq = await _resolve_reply_context(
            bot,
            group_id=group_id_str,
            reply_to_message_id=reply_to_message_id,
        )
        direct_bot_address = bool(
            mentions_bot
            or text_at
            or to_me
            or (
                reply_to_message_id
                and quoted_user_qq == str(state.bot_qq_id)
            )
        )
        addressed_elsewhere = _is_addressed_elsewhere(
            mentioned_qqs=mentioned_qqs,
            bot_qq=state.bot_qq_id,
            direct_bot_address=direct_bot_address,
            reply_to_message_id=reply_to_message_id,
            quoted_user_qq=quoted_user_qq,
            text_at_other_member=text_at_other_member,
        )
        if addressed_elsewhere:
            explicit_trigger = False
        dialogue_cfg = get_config().get("conversation_followup", {})
        followup_decision = ContinuationDecision(False, "explicit-trigger")
        if not explicit_trigger:
            followup_decision = evaluate_continuation(
                group_id=group_id_str,
                user_qq=user_qq,
                text=message_text,
                bot_qq=state.bot_qq_id,
                mentioned_qqs=mentioned_qqs,
                reply_to_message_id=reply_to_message_id,
                quoted_user_qq=quoted_user_qq,
                source_message_id=source_message_id,
                now=now_ts,
                config=dialogue_cfg,
            )
        dialogue_followup = followup_decision.candidate
        should_respond = explicit_trigger or dialogue_followup
        ttl_seconds = float(dialogue_cfg.get("timeout_seconds", 240))
        if explicit_trigger:
            state.start_dialogue_session(
                group_id_str,
                user_qq=user_qq,
                source_message_id=source_message_id,
                origin="explicit",
                now=now_ts,
                ttl_seconds=ttl_seconds,
            )
        elif dialogue_followup:
            active_dialogue = state.get_dialogue_session(group_id_str, now=now_ts)
            if (
                followup_decision.direct_reply
                and (
                    not active_dialogue
                    or str(active_dialogue.get("user_qq") or "") != user_qq
                )
            ):
                state.start_dialogue_session(
                    group_id_str,
                    user_qq=user_qq,
                    source_message_id=source_message_id,
                    origin="direct_reply",
                    now=now_ts,
                    ttl_seconds=ttl_seconds,
                )
            state.mark_dialogue_user_turn(
                group_id_str,
                user_qq=user_qq,
                now=now_ts,
                ttl_seconds=ttl_seconds,
            )
            logger.info(
                "Dialogue continuation candidate: group=%s user=%s reason=%s elapsed=%.1fs intervening=%d",
                group_id_str,
                user_qq,
                followup_decision.reason,
                followup_decision.elapsed_seconds,
                followup_decision.intervening_humans,
            )
        contextual_text = message_text
        if carried_from_previous:
            contextual_text = f"[承接该成员上一条消息]\n{contextual_text}"
        if quoted_context:
            contextual_text = f"{quoted_context}\n{contextual_text}"
        if message_id is not None and contextual_text != message_text:
            async with await get_session() as session:
                await update_message_content(
                    session,
                    message_id,
                    content=contextual_text,
                )
                await session.commit()

        state.group_last_active[group_id_str] = now_ts
        state.record_group_message(group_id_str, now=state.group_last_active[group_id_str])
        state.record_recent_group_text(
            group_id_str,
            user_qq=user_qq,
            nickname=nickname,
            text=contextual_text,
            source_message_id=source_message_id,
            mentioned_qqs=mentioned_qqs,
            reply_to_message_id=reply_to_message_id,
            carried_from_previous=carried_from_previous,
            addressed_elsewhere=addressed_elsewhere,
            now=state.group_last_active[group_id_str],
        )
        feedback_outcome = None
        if not addressed_elsewhere:
            feedback_outcome = state.observe_proactive_join_feedback(
                group_id_str,
                user_qq=user_qq,
                bot_qq=state.bot_qq_id,
                text=contextual_text,
                mentions_bot=explicit_trigger,
                reply_to_message_id=reply_to_message_id,
                now=state.group_last_active[group_id_str],
            )
        if feedback_outcome:
            logger.info(
                "Proactive join feedback: group=%s outcome=%s multiplier=%.2f",
                group_id_str,
                feedback_outcome,
                state.proactive_join_probability_multiplier(group_id_str),
            )
        from .runtime_state import schedule_persist

        schedule_persist()

        # ── 自动戳戳 ──

        async def _send_poke_action(target_qq: str):
            """发送戳一戳：先尝试 LLBot 扩展 API，失败走 CQ 码兜底"""
            try:
                await bot.call_api(
                    "send_poke",
                    user_id=int(target_qq),
                    group_id=int(group_id_str),
                )
                logger.info("Poke (api): user=%s group=%s", target_qq, group_id_str)
            except Exception:
                try:
                    await bot.send_group_msg(
                        group_id=int(group_id_str),
                        message=f"[CQ:poke,qq={target_qq}]",
                    )
                    logger.info("Poke (cq): user=%s group=%s", target_qq, group_id_str)
                except Exception as e2:
                    logger.warning("Poke failed for %s: %s", target_qq, e2)
                    raise

        def _should_poke(cooldown_minutes: float) -> bool:
            last = state.auto_poke_last_time.get(user_qq, 0)
            return (_time.time() - last) / 60 >= cooldown_minutes

        # 规则1：指定目标即时戳（短冷却）
        auto_poke_target = is_auto_poke_target(user_qq, nickname)
        if (
            not addressed_elsewhere
            and auto_poke_target
            and _should_poke(auto_poke_target.get("cooldown_minutes", 5))
        ):
            async def _do_target_poke():
                try:
                    if not await should_send_proactive_message(
                        group_id=group_id_str,
                        reason="poke_target",
                        candidate_text=f"戳一戳 {nickname or user_qq}",
                        trigger_text=text,
                    ):
                        return
                    await _send_poke_action(user_qq)
                    state.auto_poke_last_time[user_qq] = _time.time()
                except Exception:
                    pass
            asyncio.create_task(_do_target_poke())

        # 规则2：任何人发言，30 分钟内戳一次
        poke_everyone_cd = get_config().get("poke_everyone_cooldown_minutes", 0)
        if (
            not addressed_elsewhere
            and poke_everyone_cd > 0
            and _should_poke(poke_everyone_cd)
        ):
            async def _do_general_poke():
                try:
                    if not await should_send_proactive_message(
                        group_id=group_id_str,
                        reason="poke_general",
                        candidate_text=f"戳一戳 {nickname or user_qq}",
                        trigger_text=text,
                    ):
                        return
                    await _send_poke_action(user_qq)
                    state.auto_poke_last_time[user_qq] = _time.time()
                except Exception:
                    pass
            asyncio.create_task(_do_general_poke())

        # ── 图片缓存：无论是否触发回复，都缓存有效图片 ──
        image_infos = [
            {
                **image,
                "message_id": message_id,
                "source_message_id": source_message_id,
                "user_qq": user_qq,
                "timestamp": now_ts,
                "memory_content": contextual_text,
            }
            for image in _extract_images(event)
        ]
        if image_infos:
            cache = state.group_recent_images.setdefault(group_id_str, [])
            now = _time.time()
            for img in image_infos:
                cache.append({**img, "time": now})
            # 清理过期 & 超量
            state.group_recent_images[group_id_str] = [
                c for c in cache if now - c["time"] < state.IMAGE_CACHE_TTL
            ][-state.MAX_CACHED_IMAGES:]
            logger.info("[IMAGE] 缓存 %d 张图片 (群 %s 共 %d 张)",
                        len(image_infos), group_id_str,
                        len(state.group_recent_images[group_id_str]))

        if addressed_elsewhere:
            logger.info(
                "Skipping unsolicited actions for human-directed turn: group=%s "
                "user=%s mentions=%s reply_to=%s quoted_qq=%s",
                group_id_str,
                user_qq,
                other_mentions,
                reply_to_message_id or "",
                quoted_user_qq or "",
            )
            return

        correction_reply = (
            _local_turn_correction_reply(message_text) if should_respond else None
        )
        if correction_reply:
            sent_message_id = await _send_group_reply(
                bot,
                group_id_str,
                correction_reply,
            )
            state.close_dialogue_session(group_id_str, user_qq=user_qq)
            schedule_persist()
            logger.info(
                "Acknowledged mistaken turn locally: group=%s user=%s message=%s",
                group_id_str,
                user_qq,
                sent_message_id or "",
            )
            return

        # ── 天气查询：这里只记录意图，实际 HTTP 请求放到回复处理阶段并发执行 ──
        weather_q = ""
        weather_text = message_text if should_respond else None
        if weather_text:
            weather_q = _is_weather_query(weather_text) or ""
            if weather_q:
                logger.info("[Weather] enqueued for group %s: '%s'", group_id_str, weather_q[:60])

        if should_respond:
            effective_text = contextual_text

            # 检测特殊指令模式
            check_text = message_text
            special_mode, special_target, _ = _detect_special_mode(check_text or "")

            if special_mode != "normal":
                effective_text = f"[指令模式: {special_mode}] 目标: {special_target or '无'}"
                if special_mode == "joke":
                    effective_text = "[指令模式: joke] 讲一个冷笑话"

            # ── 智能联网搜索：将搜索文本随消息传递到 _process_messages_inner ──
            search_text = message_text
            clean_search = ""
            if search_text in {"[非文本群消息]", "[有成员@了小源]"}:
                search_text = ""
            if search_text:
                bot_names = get_config().get("bot", {}).get("nickname", "小源")
                bot_names_list = [bot_names] if isinstance(bot_names, str) else bot_names
                for name in bot_names_list + ["开源协会", "协会"]:
                    search_text = re.sub(rf"^{re.escape(name)}\s*", "", search_text)
                clean_search = search_text.strip()
                if clean_search:
                    logger.info(
                        "[Search] candidate passed to planner for group %s: '%s'",
                        group_id_str,
                        clean_search[:60],
                    )

            # ── 图片识别 ──
            # 1) 当前消息带图：随消息入队，在群级回复管线内识别
            # 2) 用户提到图片但消息里没图：从缓存中取该用户最近发的图
            trigger_images = image_infos  # 当前消息里的图

            if not trigger_images:
                # 检测是否在请求识图
                img_keywords = ["图片", "图", "看图", "识图", "识别", "发的图", "这个图", "这张图", "那个图", "那张图"]
                check_for_img = message_text
                if check_for_img and any(kw in check_for_img for kw in img_keywords):
                    cache = state.group_recent_images.get(group_id_str, [])
                    now = _time.time()
                    # 取同一用户最近 3 张图
                    user_imgs = [c for c in cache
                                 if c["user_qq"] == user_qq and now - c["time"] < state.IMAGE_CACHE_TTL]
                    if user_imgs:
                        trigger_images = [dict(user_imgs[-1])]
                        logger.info("[IMAGE] 从缓存取出 %s 最近的图片", user_qq)

            if message_id is not None and effective_text != contextual_text:
                async with await get_session() as session:
                    await update_message_content(
                        session,
                        message_id,
                        content=effective_text,
                    )
                    await session.commit()

            silent = get_silent_window()
            fast_window = get_config().get("silent_window", {}).get("explicit_group_seconds", 0.8)
            followup_window = float(dialogue_cfg.get("window_seconds", 0.9))
            recent_context = state.format_recent_group_flow(
                group_id_str,
                limit=int(
                    dialogue_cfg.get(
                        "recent_context_messages",
                        get_effective_proactive_join_config(
                            group_id_str,
                            config=get_config(),
                        )
                        .get("recent_context_messages", 8),
                    )
                ),
                exclude_source_message_id=source_message_id,
                now=now_ts,
            )
            silent.enqueue(
                group_key(group_id_str),
                {
                    "scene": "group", "target_id": user_qq,
                    "group_id": group_id_str, "user_qq": user_qq,
                    "text": effective_text,
                    "timestamp": _time.time(),
                    "mode": special_mode,
                    "mode_target": special_target,
                    "explicit_trigger": explicit_trigger,
                    "dialogue_followup": dialogue_followup,
                    "dialogue_instruction": build_continuation_instruction(
                        followup_decision
                    ),
                    "dialogue_reason": followup_decision.reason,
                    "dialogue_close_after_reply": (
                        followup_decision.close_after_reply
                        if dialogue_followup
                        else is_closing_message(message_text)
                    ),
                    "images": trigger_images,
                    "search_text": clean_search,
                    "weather_query": weather_q,
                    "message_id": message_id,
                    "ambient_context": recent_context,
                    "source_message_id": source_message_id,
                    "reply_to_message_id": reply_to_message_id,
                    "quoted_user_qq": quoted_user_qq,
                    "carried_from_previous": carried_from_previous,
                },
                is_group=True,
                wait_seconds=(
                    fast_window
                    if explicit_trigger
                    else followup_window if dialogue_followup else None
                ),
            )

        proactive_join_claimed = False

        # 复读检测：接住“大家在复读”这个现象，而不是机械复制同一句。
        if text and not should_respond:
            repeat_text = await check_repeat(group_id_str, text)
            if repeat_text:
                repeat_reply = _choose_repeat_wave_reaction(
                    avoid=state.get_recent_bot_texts(group_id_str),
                )
                if await should_send_proactive_message(
                    group_id=group_id_str,
                    reason="repeat",
                    candidate_text=repeat_reply,
                    trigger_text=text,
                ):
                    sent_message_id = await _send_group_reply(
                        bot,
                        group_id_str,
                        repeat_reply,
                    )
                    state.mark_proactive_join_sent(
                        group_id_str,
                        source_message_id=sent_message_id,
                    )
                    schedule_persist()
                    proactive_join_claimed = True

        # 主动接话 & 戳戳
        if not should_respond and text:
            topic = detect_interesting_topic(text)
            if topic:
                # 戳一戳：遇到感兴趣话题戳一下发言者（独立于接话，有自己冷却）
                if user_qq:
                    asyncio.create_task(
                        try_poke_topic(group_id_str, user_qq, topic)
                    )
        # 关键词反应 / 弔图语录
        if not should_respond and text and not proactive_join_claimed:
            join_cfg = get_effective_proactive_join_config(
                group_id_str,
                config=get_config(),
            )
            if join_cfg.get("enabled", True):
                now_ts = _time.time()
                min_cooldown = float(join_cfg.get("min_cooldown_seconds", 600))
                last_join = state.proactive_join_last_time.get(group_id_str, 0)
                cooldown_remaining = min_cooldown - (now_ts - last_join)
                if cooldown_remaining > 0:
                    logger.info(
                        "Proactive join skipped: group=%s reason=cooldown remaining=%.1fs",
                        group_id_str, cooldown_remaining,
                    )
                    _trace_proactive(
                        "cooldown",
                        group_id_str,
                        remaining=f"{cooldown_remaining:.1f}s",
                    )
                else:
                    group_times = state.trim_recent_times(
                        state.group_message_times, group_id_str, now=now_ts,
                    )
                    bot_times = state.trim_recent_times(
                        state.bot_reply_times, group_id_str, now=now_ts,
                    )
                    last_bot = bot_times[-1] if bot_times else 0
                    human_since_bot = (
                        sum(1 for item in group_times if item > last_bot)
                        if last_bot
                        else len(group_times)
                    )
                    join_decision = decide_join_opportunity(
                        JoinOpportunitySignals(
                            trigger_text=text,
                            topic_match=bool(detect_interesting_topic(text)),
                            quiet_hours=is_quiet_hours(),
                            messages_last_5m=len(group_times),
                            bot_messages_last_5m=len(bot_times),
                            seconds_since_bot_reply=(
                                now_ts - last_bot if last_bot else 9999.0
                            ),
                            human_messages_since_bot=human_since_bot,
                            score_bonus=int(join_cfg.get("score_bonus", 0)),
                            max_bot_messages_5m=int(
                                join_cfg.get("max_bot_messages_5m", 2)
                            ),
                            min_human_turns_after_bot=int(
                                join_cfg.get("min_human_turns_after_bot", 3)
                            ),
                        )
                    )
                    logger.info(
                        "Proactive join scored: group=%s action=%s score=%s reason=%s humans5m=%s bot5m=%s",
                        group_id_str,
                        join_decision.action,
                        join_decision.score,
                        join_decision.reason,
                        len(group_times),
                        len(bot_times),
                    )
                    _trace_proactive(
                        "scored",
                        group_id_str,
                        action=join_decision.action,
                        score=join_decision.score,
                        humans5m=len(group_times),
                        bot5m=len(bot_times),
                        reason=join_decision.reason,
                    )
                    if join_decision.action != "silent":
                        probability = _join_probability(
                            join_decision.action, join_cfg, group_id_str,
                        )
                        probability_roll = random.random()
                        if probability_roll >= probability:
                            logger.info(
                                "Proactive join skipped: group=%s reason=probability action=%s roll=%.3f threshold=%.3f",
                                group_id_str,
                                join_decision.action,
                                probability_roll,
                                probability,
                            )
                            _trace_proactive(
                                "probability_skip",
                                group_id_str,
                                action=join_decision.action,
                                roll=f"{probability_roll:.3f}",
                                threshold=f"{probability:.3f}",
                            )
                        else:
                            decision_payload = join_decision.to_payload()
                            recent_context = state.format_recent_group_flow(
                                group_id_str,
                                limit=int(join_cfg.get("recent_context_messages", 8)),
                                exclude_source_message_id=source_message_id,
                                now=now_ts,
                            )
                            candidate = _join_candidate_text(
                                text,
                                detect_interesting_topic(text),
                                join_decision.action,
                            )
                            local_reply = None
                            if (
                                join_decision.action == "react"
                                and join_cfg.get("local_reactions_enabled", True)
                            ):
                                local_reply = _choose_local_light_reaction(
                                    text,
                                    avoid=state.get_recent_bot_texts(group_id_str),
                                )
                                if local_reply:
                                    candidate = local_reply
                            if local_reply:
                                local_approved = await should_send_proactive_message(
                                    group_id=group_id_str,
                                    reason=_join_reason_for_action(join_decision.action),
                                    candidate_text=candidate,
                                    trigger_text=text,
                                    recent_context=recent_context,
                                )
                                if local_approved:
                                    current_local_msg = {
                                        "group_id": group_id_str,
                                        "user_qq": user_qq,
                                        "timestamp": now_ts,
                                        "source_message_id": source_message_id,
                                        "join_instruction": _format_join_instruction(
                                            decision_payload
                                        ),
                                        "join_action": join_decision.action,
                                    }
                                    if await _wait_for_natural_send_timing(
                                        local_reply,
                                        group_id=group_id_str,
                                        current_msg=current_local_msg,
                                        explicit_trigger=False,
                                        proactive=True,
                                    ):
                                        sent_message_id = await _send_group_reply(
                                            bot, group_id_str, local_reply,
                                        )
                                        state.mark_proactive_join_sent(
                                            group_id_str,
                                            source_message_id=sent_message_id,
                                        )
                                        schedule_persist()
                                        proactive_join_claimed = True
                                        logger.info(
                                            "Local proactive reaction sent: group=%s action=%s score=%s",
                                            group_id_str,
                                            join_decision.action,
                                            join_decision.score,
                                        )
                                        _trace_proactive(
                                            "local_sent",
                                            group_id_str,
                                            action=join_decision.action,
                                            score=join_decision.score,
                                        )
                                else:
                                    logger.info(
                                        "Local proactive reaction rejected by AI gate: group=%s action=%s",
                                        group_id_str, join_decision.action,
                                    )
                                    _trace_proactive(
                                        "local_ai_rejected",
                                        group_id_str,
                                        action=join_decision.action,
                                    )
                            else:
                                # The generation LLM is the single contextual AI gate for
                                # generated joins and can return the internal [SILENT] marker.
                                get_silent_window().enqueue(
                                    group_key(group_id_str),
                                    {
                                        "scene": "group",
                                        "target_id": user_qq,
                                        "group_id": group_id_str,
                                        "user_qq": user_qq,
                                        "text": text,
                                        "timestamp": now_ts,
                                        "mode": "normal",
                                        "mode_target": "",
                                        "explicit_trigger": False,
                                        "search_text": "",
                                        "weather_query": "",
                                        "message_id": message_id,
                                        "source_message_id": source_message_id,
                                        "join_instruction": _format_join_instruction(
                                            decision_payload
                                        ),
                                        "join_max_chars": join_decision.max_chars,
                                        "join_action": join_decision.action,
                                        "ambient_context": recent_context,
                                    },
                                    is_group=True,
                                    wait_seconds=float(join_cfg.get("window_seconds", 1.5)),
                                )
                                proactive_join_claimed = True
                                logger.info(
                                    "Proactive join queued for contextual AI gate: group=%s action=%s score=%s reason=%s",
                                    group_id_str,
                                    join_decision.action,
                                    join_decision.score,
                                    join_decision.reason,
                                )
                                _trace_proactive(
                                    "queued",
                                    group_id_str,
                                    action=join_decision.action,
                                    score=join_decision.score,
                                )

        if not should_respond and text and not proactive_join_claimed:
            reaction = check_reaction(text, group_id_str)
            if reaction:
                if await should_send_proactive_message(
                    group_id=group_id_str,
                    reason="reaction",
                    candidate_text=reaction,
                    trigger_text=text,
                ):
                    await asyncio.sleep(1)
                    await _send_group_reply(bot, group_id_str, reaction)


# ─── 事件注册 ──────────────────────────────────────────────────────────────────

msg_handler = on_message(priority=10, block=False)


@msg_handler.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    await _on_message(event)


# ─── 戳一戳 ──────────────────────────────────────────────────────────────────

_POKE_REPLIES = (
    "看到了，别连戳",
    "又戳我干嘛",
    "在呢在呢",
    "好好好，注意到你了",
    "手下留情",
)


def _poke_reply_allowed(
    group_id: str,
    user_qq: str,
    *,
    now: float | None = None,
    cfg: dict | None = None,
) -> tuple[bool, str]:
    if now is None:
        now = _time.time()
    poke_cfg = cfg if cfg is not None else get_config().get("poke_reply", {})
    if poke_cfg.get("enabled", True) is False:
        return False, "disabled"
    group_cooldown = max(0.0, float(poke_cfg.get("group_cooldown_seconds", 360)))
    user_cooldown = max(0.0, float(poke_cfg.get("user_cooldown_seconds", 600)))
    after_bot = max(0.0, float(poke_cfg.get("after_bot_reply_seconds", 45)))
    if now - float(state.poke_reply_group_last_time.get(group_id, 0)) < group_cooldown:
        return False, "group cooldown"
    user_key = f"{group_id}:{user_qq}"
    if now - float(state.poke_reply_user_last_time.get(user_key, 0)) < user_cooldown:
        return False, "user cooldown"
    recent_bot_times = state.trim_recent_times(state.bot_reply_times, group_id, now=now)
    if recent_bot_times and now - float(recent_bot_times[-1]) < after_bot:
        return False, "bot just spoke"
    return True, "allowed"


def _choose_poke_reply(
    recent_replies: list[str] | tuple[str, ...] | None = None,
    *,
    chooser=random.choice,
) -> str:
    avoided = {(item or "").strip() for item in (recent_replies or [])}
    available = [reply for reply in _POKE_REPLIES if reply not in avoided]
    return str(chooser(available or list(_POKE_REPLIES)))


poke_handler = on_notice(priority=10, block=False)


@poke_handler.handle()
async def handle_notice(bot: Bot, event: NoticeEvent):
    nt = getattr(event, "notice_type", "")
    st = getattr(event, "sub_type", "")
    target = getattr(event, "target_id", None)
    gid = getattr(event, "group_id", None)
    if nt == "group_recall" and gid:
        group_id = str(gid)
        if not _is_allowed_group(group_id):
            return
        source_message_id = str(getattr(event, "message_id", "") or "")
        if not source_message_id:
            return
        state.record_group_recall(group_id, source_message_id)
        try:
            forgotten_id = await forget_inbound_memory(
                group_id=group_id,
                source_message_id=source_message_id,
            )
            logger.info(
                "Group recall removed memory: group=%s source=%s message=%s",
                group_id,
                source_message_id,
                forgotten_id,
            )
        except Exception:
            logger.exception(
                "Failed to forget recalled message: group=%s source=%s",
                group_id,
                source_message_id,
            )
        from .runtime_state import schedule_persist

        schedule_persist()
        return
    if nt != "notify" or st != "poke":
        return
    if str(target) != str(bot.self_id):
        return
    if gid:
        group_id = str(gid)
        if not _is_allowed_group(group_id):
            return
        user_qq = str(getattr(event, "user_id", "") or "")
        now = _time.time()
        allowed, reason = _poke_reply_allowed(
            group_id,
            user_qq,
            now=now,
        )
        if not allowed:
            logger.info(
                "Poke reply skipped: group=%s user=%s reason=%s",
                group_id,
                user_qq,
                reason,
            )
            return
        reply = _choose_poke_reply(state.get_recent_bot_texts(group_id))
        await _send_group_reply(bot, group_id, reply)
        state.poke_reply_group_last_time[group_id] = now
        state.poke_reply_user_last_time[f"{group_id}:{user_qq}"] = now
        from .runtime_state import schedule_persist

        schedule_persist()


def setup_silent_callback():
    get_silent_window().set_callback(_process_messages)
