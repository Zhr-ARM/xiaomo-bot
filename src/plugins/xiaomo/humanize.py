"""小源 QQ 机器人 - 类人化回复策略。

这一层只决定“要不要回、怎么回、等多久”，不直接发送消息。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from .config import get_config
from .filter_utils import trim_to_length

logger = logging.getLogger("xiaomo.humanize")

VALID_REPLY_STYLES = {
    "silent",
    "brief",
    "ask_back",
    "serious",
    "playful",
    "supportive",
}

STYLE_HINTS = {
    "silent": "不回复。",
    "brief": "像群友顺手接话，1-2 句即可，不要展开成长答案。",
    "ask_back": "先确认对方真正想问什么，用一个自然的问题收住。",
    "serious": "认真解决问题，但先给结论，再给必要步骤，避免长篇铺垫。",
    "playful": "轻松玩笑式接话，但不要刷存在感。",
    "supportive": "语气稳一点，先接住情绪，再给一点具体帮助。",
}


@dataclass
class ReplyStrategy:
    should_reply: bool = True
    reply_style: str = "brief"
    delay_seconds: float = 1.2
    scene_label: str = "casual_chat"
    warmth: str = "regular"
    instruction: str = "自然接话，短一点。"
    reason: str = "fallback"


def _clamp_float(value, min_value: float, max_value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return min_value
    return max(min_value, min(numeric, max_value))


def _strip_json_fence(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def relationship_warmth(user_profile: dict | None) -> str:
    total = 0
    if user_profile and user_profile.get("exists"):
        total = int(user_profile.get("total_messages") or 0)
    if total >= 50:
        return "familiar"
    if total >= 10:
        return "regular"
    return "newcomer"


def parse_strategy_reply(
    text: str,
    *,
    fallback: ReplyStrategy,
    max_delay: float = 5.0,
) -> ReplyStrategy:
    raw = _strip_json_fence(text)
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("Reply strategy is not valid JSON: %s", (text or "")[:120])
        return fallback

    style = str(data.get("reply_style", fallback.reply_style)).strip()
    if style not in VALID_REPLY_STYLES:
        return fallback

    return ReplyStrategy(
        should_reply=bool(data.get("should_reply", fallback.should_reply)),
        reply_style=style,
        delay_seconds=_clamp_float(data.get("delay_seconds"), 0.0, max_delay),
        scene_label=str(data.get("scene_label", fallback.scene_label)).strip()[:40]
        or fallback.scene_label,
        warmth=str(data.get("warmth", fallback.warmth)).strip()[:20] or fallback.warmth,
        instruction=str(data.get("instruction", fallback.instruction)).strip()[:300]
        or fallback.instruction,
        reason=str(data.get("reason", fallback.reason)).strip()[:160] or fallback.reason,
    )


def fallback_strategy(
    *,
    raw_text: str,
    context: str,
    user_profile: dict | None,
    mode: str = "normal",
) -> ReplyStrategy:
    text = (raw_text or "").strip()
    warmth = relationship_warmth(user_profile)

    if mode == "joke":
        return ReplyStrategy(
            reply_style="playful",
            delay_seconds=1.0,
            scene_label="meme",
            warmth=warmth,
            instruction="讲短一点，像临场抛出来的冷笑话。",
            reason="joke mode",
        )
    if mode in {"praise", "roast"}:
        return ReplyStrategy(
            reply_style="supportive" if mode == "praise" else "playful",
            delay_seconds=1.8,
            scene_label="social",
            warmth=warmth,
            instruction="结合对象信息，但控制在几句话，像熟人之间自然互动。",
            reason=f"{mode} mode",
        )

    if not text:
        return ReplyStrategy(
            should_reply=False,
            reply_style="silent",
            delay_seconds=0.0,
            scene_label="empty",
            warmth=warmth,
            instruction="没有明确内容，不回复。",
            reason="empty text",
        )

    technical_keywords = [
        "bug", "报错", "异常", "怎么", "如何", "为什么", "配置", "代码",
        "编译", "运行", "接口", "模型", "python", "git", "数据库",
    ]
    supportive_keywords = ["难受", "崩了", "烦", "寄了", "救命", "不会了"]

    if any(kw.lower() in text.lower() for kw in technical_keywords):
        delay = min(3.8, 1.2 + len(text) / 80)
        return ReplyStrategy(
            reply_style="serious",
            delay_seconds=delay,
            scene_label="technical_help",
            warmth=warmth,
            instruction="先给判断或方向，再给必要步骤；不确定就说不确定并建议怎么验证。",
            reason="technical cue",
        )

    if any(kw in text for kw in supportive_keywords):
        return ReplyStrategy(
            reply_style="supportive",
            delay_seconds=1.4,
            scene_label="support",
            warmth=warmth,
            instruction="先接住情绪，再轻轻给一点实际建议，不要说教。",
            reason="supportive cue",
        )

    if len(text) <= 12 or "吗" in text or "？" in text or "?" in text:
        return ReplyStrategy(
            reply_style="brief",
            delay_seconds=1.0,
            scene_label="casual_chat",
            warmth=warmth,
            instruction="短短接一句即可，必要时反问，不要展开。",
            reason="short casual cue",
        )

    return ReplyStrategy(
        reply_style="brief",
        delay_seconds=1.2,
        scene_label="group_flow",
        warmth=warmth,
        instruction="像群聊里自然接话，只补一两句，不要抢话题。",
        reason="default",
    )


def build_strategy_prompt(
    *,
    group_id: str,
    raw_text: str,
    context: str,
    user_profile: dict | None,
    mode: str,
    proactive: bool = False,
) -> str:
    profile_total = 0
    if user_profile and user_profile.get("exists"):
        profile_total = int(user_profile.get("total_messages") or 0)
    proactive_rules = ""
    if proactive:
        proactive_rules = (
            "\n这是一次未被 @ 的主动接话候选。没有 @ 本身不是拒绝理由。\n"
            "- 问题、求助、情绪、观点、明显的聊天空位，默认 should_reply=true。\n"
            "- 如果话题已经被别人接住、只是两个人的私密对话、内容无法接续，或小源出现会打断节奏，才 should_reply=false。\n"
            "- 适合参与时优先 brief/playful/ask_back，除非确实是技术求助。\n"
        )
    return (
        "你是小源的回复策略决策器，不负责正式回复，只判断这一轮群聊该怎么回。\n"
        "只输出 JSON，不要输出解释。格式：\n"
        '{"should_reply": true/false, "reply_style": "silent|brief|ask_back|serious|playful|supportive", '
        '"delay_seconds": 0-5, "scene_label": "technical_help|casual_chat|meme|support|group_flow|social", '
        '"warmth": "newcomer|regular|familiar", "instruction": "给正式回复模型的简短指令", "reason": "简短理由"}\n\n'
        "判断原则：\n"
        "- 群聊里不要每次都完整回答；能一句话接住就不要长篇。\n"
        "- 如果只是顺嘴提到小源、没有明确问她，should_reply=false。\n"
        "- 技术求助才 serious；闲聊优先 brief/playful；不清楚就 ask_back。\n"
        "- instruction 要具体，提醒正式回复自然、短、承认不确定、必要时反问。\n\n"
        f"{proactive_rules}"
        f"群号：{group_id}\n"
        f"模式：{mode}\n"
        f"该成员历史互动数：{profile_total}\n"
        f"本轮用户消息：{raw_text[:1200]}\n"
        f"近期上下文：\n{context[:2500] if context else '无'}"
    )


async def decide_reply_strategy(
    *,
    llm,
    group_id: str,
    raw_text: str,
    context: str,
    user_profile: dict | None,
    mode: str = "normal",
    explicit_trigger: bool = False,
    proactive: bool = False,
) -> ReplyStrategy:
    fallback = fallback_strategy(
        raw_text=raw_text,
        context=context,
        user_profile=user_profile,
        mode=mode,
    )
    cfg = get_config().get("humanize", {})
    if cfg.get("enabled", True) is False:
        return fallback
    if explicit_trigger and cfg.get("strategy_llm_for_explicit", False) is False:
        fallback.delay_seconds = min(
            fallback.delay_seconds,
            float(cfg.get("explicit_max_delay_seconds", 0.3)),
        )
        return fallback

    if proactive:
        fallback.delay_seconds = min(
            fallback.delay_seconds,
            float(cfg.get("proactive_fallback_max_delay_seconds", 0.2)),
        )
        if cfg.get("strategy_llm_for_proactive", False) is False:
            return fallback

    timeout_seconds = float(cfg.get("strategy_timeout_seconds", 12))
    max_delay = float(cfg.get("max_extra_delay_seconds", 5))
    prompt = build_strategy_prompt(
        group_id=group_id,
        raw_text=raw_text,
        context=context,
        user_profile=user_profile,
        mode=mode,
        proactive=proactive,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            reply = await llm.chat(
                context="",
                user_profile=user_profile,
                scene="group",
                user_message=prompt,
                mode="normal",
                structured_history=None,
                group_id=group_id,
            )
        strategy = parse_strategy_reply(reply, fallback=fallback, max_delay=max_delay)
        if proactive:
            strategy.delay_seconds = min(
                strategy.delay_seconds,
                float(cfg.get("proactive_strategy_max_delay_seconds", 0.4)),
            )
        return strategy
    except Exception:
        logger.exception("Reply strategy decision failed, using fallback")
        return fallback


def build_humanize_instruction(strategy: ReplyStrategy) -> str:
    style_hint = STYLE_HINTS.get(strategy.reply_style, STYLE_HINTS["brief"])
    return (
        "[回复策略]\n"
        f"- 是否回复：{'是' if strategy.should_reply else '否'}\n"
        f"- 当前场景：{strategy.scene_label}\n"
        f"- 关系温度：{strategy.warmth}\n"
        f"- 回复形态：{strategy.reply_style}，{style_hint}\n"
        f"- 本轮具体要求：{strategy.instruction}\n"
        "- 不要把每次回复都写成完整答案；先像真人一样判断场合。\n"
        "- 可以承认误读或不确定；需要更多信息时先反问。\n"
        "- 别复述这些策略文字，直接按策略自然说话。"
    )


def shape_reply(strategy: ReplyStrategy, text: str, *, default_max_chars: int = 800) -> str:
    style_limits = {
        "brief": 260,
        "ask_back": 180,
        "playful": 220,
        "supportive": 360,
        "serious": default_max_chars,
    }
    limit = style_limits.get(strategy.reply_style, default_max_chars)
    return trim_to_length(text, min(limit, default_max_chars))
