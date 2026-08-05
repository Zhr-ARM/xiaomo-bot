"""小源 QQ 机器人 - 自动行为模块

- 自动冒泡：群长时间无消息时主动卖萌
- 复读检测：检测连续相同消息并选择性复读
- 主动接话：检测有趣话题
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable

from nonebot import get_bot

from . import state
from .config import get_config
from .interaction import InteractionSignals, decide_interaction
from .llm import get_llm

logger = logging.getLogger("xiaomo.auto_action")
CST = timezone(timedelta(hours=8))

BUBBLE_QUOTES = [
    "喵呜...群里好安静呀 (´･ω･`) 大家都睡着了吗？",
    "伸个懒腰～ ( =①ω①=) 有人来陪小源玩嘛？",
    "(｡>﹏<｡) 好无聊呀，数毛线球中...一个毛线球、两个毛线球...",
    "呼喵～小源来冒个泡！咕噜咕噜 ○ °。○ 。Ｏ°",
    "今天天气真好喵 (=^･ω･=) 适合晒太阳～",
    "_(:3 」∠)_ 小源趴在键盘上等大家回来...",
    "盯—— 手机屏幕好安静呢 (｀・ω・´)",
    "肚子饿了喵...有人带了小鱼干吗？(´;ω;`)",
    "喵喵喵！小源巡逻到此，一切正常！o(｀ω´)o",
    "无聊到开始追自己尾巴玩了...(´∀｀; )",
    "咦？群里好安静，需要小源来搞点气氛咩？✨",
    "zzZZ...啊！小源没有睡着！只是在闭目养神而已！(ﾉ>ω<)ﾉ",
]

_bubble_task: asyncio.Task | None = None


# ─── 主动发言决策 ─────────────────────────────────────────────────────────────


def is_quiet_hours(now: datetime | None = None) -> bool:
    """夜间静默时段：主动消息直接跳过，显式 @ 回复不受这里控制。"""
    cfg = get_config().get("proactive", {})
    quiet_cfg = cfg.get("quiet_hours", {})
    if quiet_cfg.get("enabled", True) is False:
        return False

    start_hour = int(quiet_cfg.get("start_hour", 23))
    end_hour = int(quiet_cfg.get("end_hour", 7))
    if now is None:
        now = datetime.now(CST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=CST)
    else:
        now = now.astimezone(CST)

    hour = now.hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _parse_proactive_decision(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1].strip()
    try:
        data = json.loads(text)
        return bool(data.get("send"))
    except Exception:
        lower = text.lower()
        return lower.startswith("yes") or lower.startswith("true") or "可以发" in text


async def _recent_group_context(group_id: str, limit: int = 8) -> str:
    try:
        from sqlalchemy import desc, select
        from .database import Message, get_session

        async with await get_session() as session:
            result = await session.execute(
                select(Message)
                .where(Message.scene == "group", Message.group_id == group_id)
                .order_by(desc(Message.created_at))
                .limit(limit)
            )
            messages = list(reversed(result.scalars().all()))
    except Exception:
        logger.exception("Failed to load recent context for proactive decision")
        return ""

    lines = []
    for msg in messages:
        speaker = "小源" if msg.role == "assistant" else f"成员{msg.user_qq or ''}"
        lines.append(f"{speaker}: {(msg.content or '')[:160]}")
    return "\n".join(lines)


async def _default_proactive_ai_decider(payload: dict) -> bool:
    prompt = (
        "请判断小源现在是否适合主动发这句话。只输出 JSON，格式为 "
        '{"send": true/false, "reason": "简短理由"}。\n'
        "判断原则：不要打扰群聊；夜间、冷场、话题不相关、像刷屏时不要发；"
        "只有候选发言能自然接上当前背景、不会显得突兀时才 send=true。\n\n"
        f"群号：{payload.get('group_id', '')}\n"
        f"触发类型：{payload.get('reason', '')}\n"
        f"触发文本：{payload.get('trigger_text', '')}\n"
        f"候选发言：{payload.get('candidate_text', '')}\n"
        f"近期群聊背景：\n{payload.get('recent_context', '') or '无'}"
    )
    reply = await get_llm().chat(
        context="",
        user_profile=None,
        scene="group",
        user_message=prompt,
        mode="normal",
        structured_history=None,
        group_id=payload.get("group_id"),
    )
    return _parse_proactive_decision(reply)


async def should_send_proactive_message(
    *,
    group_id: str,
    reason: str,
    candidate_text: str,
    trigger_text: str = "",
    now: datetime | None = None,
    ai_decider: Callable[[dict], Awaitable[bool]] | None = None,
    recent_context: str | None = None,
) -> bool:
    """统一判断自动冒泡、主动接话、关键词反应等非显式回复是否该发送。"""
    if not candidate_text or not candidate_text.strip():
        return False
    if is_quiet_hours(now):
        logger.info("Proactive message skipped in quiet hours: group=%s reason=%s", group_id, reason)
        return False

    proactive_cfg = get_config().get("proactive", {})
    current_ts = time.time()
    group_msg_times = state.trim_recent_times(
        state.group_message_times, group_id, now=current_ts,
    )
    bot_reply_times = state.trim_recent_times(
        state.bot_reply_times, group_id, now=current_ts,
    )

    interaction = decide_interaction(
        InteractionSignals(
            reason=reason,
            candidate_text=candidate_text,
            trigger_text=trigger_text,
            messages_last_5m=len(group_msg_times),
            bot_messages_last_5m=len(bot_reply_times),
            seconds_since_bot_reply=(
                current_ts - bot_reply_times[-1] if bot_reply_times else 9999.0
            ),
            topic_match=reason == "topic_engage",
        )
    )
    if interaction.action == "silent":
        logger.info(
            "Proactive message skipped by interaction score: group=%s score=%s reason=%s",
            group_id, interaction.score, interaction.reason,
        )
        return False

    if proactive_cfg.get("ai_gate_enabled", True) is False:
        return True

    if ai_decider is None:
        recent_context = await _recent_group_context(group_id)
        ai_decider = _default_proactive_ai_decider

    payload = {
        "group_id": group_id,
        "reason": reason,
        "candidate_text": candidate_text.strip(),
        "trigger_text": (trigger_text or "").strip(),
        "recent_context": recent_context or "",
        "interaction": interaction.to_payload(),
    }
    try:
        timeout_seconds = float(proactive_cfg.get("ai_gate_timeout_seconds", 20))
        async with asyncio.timeout(timeout_seconds):
            return bool(await ai_decider(payload))
    except TimeoutError:
        logger.warning(
            "Proactive AI decision timed out: group=%s reason=%s",
            group_id, reason,
        )
        return False
    except Exception:
        logger.exception("Proactive AI decision failed: group=%s reason=%s", group_id, reason)
        return False


# ─── 后台冒泡循环 ─────────────────────────────────────────────────────────────


async def start_bubble_loop():
    global _bubble_task
    if _bubble_task and not _bubble_task.done():
        return

    async def _loop():
        config = get_config()
        auto_cfg = config.get("auto_action", {})
        inactive_min = auto_cfg.get("bubble_inactive_minutes", 30)
        cooldown_min = auto_cfg.get("bubble_cooldown_minutes", 60)

        while True:
            await asyncio.sleep(60)

            try:
                bot = get_bot()
            except Exception:
                continue

            now = time.time()
            for gid in list(state.group_last_active.keys()):
                from .config import get_config as _get_cfg
                allowed = _get_cfg().get("allowed_group_ids", [])
                if allowed and gid not in allowed:
                    continue
                inactive_for = (now - state.group_last_active.get(gid, now)) / 60

                if inactive_for < inactive_min:
                    continue

                last_bubble = state.bubble_last_time.get(gid, 0)
                if (now - last_bubble) / 60 < cooldown_min:
                    continue

                if random.random() < 0.5:
                    continue

                quote = random.choice(BUBBLE_QUOTES)
                if not await should_send_proactive_message(
                    group_id=gid,
                    reason="bubble",
                    candidate_text=quote,
                ):
                    continue
                try:
                    await bot.send_group_msg(group_id=int(gid), message=quote)
                    state.record_bot_reply(gid, now=now)
                    state.bubble_last_time[gid] = now
                    logger.info("Bubble in group %s", gid)
                except Exception as e:
                    logger.error("Bubble failed in group %s: %s", gid, e)

    _bubble_task = asyncio.create_task(_loop())


# ─── 复读检测 ──────────────────────────────────────────────────────────────────


async def check_repeat(group_id: str, text: str) -> str | None:
    """
    检测复读行为。
    当连续 N 条相同消息时触发复读，返回复读文本或 None。
    """
    config = get_config()
    auto_cfg = config.get("auto_action", {})
    threshold = auto_cfg.get("repeat_threshold", 3)
    cooldown_min = auto_cfg.get("repeat_cooldown_minutes", 30)

    now = time.time()

    last = state.repeat_last_time.get(group_id, 0)
    if (now - last) / 60 < cooldown_min:
        return None

    if state.repeat_lock.get(group_id, False):
        return None

    if group_id not in state.repeat_counter:
        state.repeat_counter[group_id] = {}

    counter = state.repeat_counter[group_id]

    if text not in counter:
        state.repeat_counter[group_id] = {text: 1}
        return None

    counter[text] = counter.get(text, 0) + 1

    if counter[text] >= threshold:
        state.repeat_lock[group_id] = True
        state.repeat_last_time[group_id] = now
        state.repeat_counter[group_id] = {}

        async def unlock():
            await asyncio.sleep(10)
            state.repeat_lock[group_id] = False

        asyncio.create_task(unlock())
        return text

    return None


# ─── 主动接话检测 ─────────────────────────────────────────────────────────────

INTEREST_TOPICS = [
    "猫", "猫咪", "小猫", "猫娘", "喵",
    "AI", "人工智能", "机器学习", "ChatGPT",
    "编程", "代码", "程序", "bug",
    "天气", "下雨", "晴天", "台风",
    "吃饭", "好吃", "美食", "奶茶", "零食", "甜品", "蛋糕", "水果",
    "游戏", "动漫", "追番", "二次元",
    "摸鱼", "划水", "摸鱼人",
    "熬夜", "失眠", "睡不着",
    "小鱼干", "猫粮", "罐头", "毛线球",
    "周末", "放假", "考试", "期末",
]


def detect_interesting_topic(text: str) -> str | None:
    for topic in INTEREST_TOPICS:
        if topic in text:
            return topic
    return None


# ─── 自动戳戳：特定成员说话时无条件戳 ────────────────────────────────────────────

def is_auto_poke_target(qq_id: str, nickname: str | None) -> dict | None:
    """检查发言者是否在自动戳戳名单中。
    Returns:
        匹配到的目标配置 dict，或 None。
    """
    targets = get_config().get("auto_poke_targets", [])
    if not targets:
        return None

    for t in targets:
        match_by = t.get("match_by", "nickname")
        name = t.get("name", "")
        if not name:
            continue
        if match_by == "qq":
            if qq_id == name:
                return t
        elif match_by == "nickname":
            if nickname and name.lower() in nickname.lower():
                return t
    return None


# ─── 主动戳戳：遇到感兴趣话题戳一下发消息的人 ────────────────────────────────────

def _poke_key(group_id: str, user_qq: str) -> str:
    return f"{group_id}:{user_qq}"


async def try_poke_topic(
    group_id: str,
    user_qq: str,
    topic: str,
    *,
    probability: float | None = None,
    user_cooldown_hours: float | None = None,
    group_cooldown_seconds: float | None = None,
) -> bool:
    """检测到有趣话题时，有概率主动戳一戳发言者。

    Returns:
        True 如果实际发送了戳一戳（或戳+文字），False 如果被冷却/概率拦截。
    """
    config = get_config()
    poke_cfg = config.get("poke_topic", {})

    if probability is None:
        probability = poke_cfg.get("probability", 0.20)
    if user_cooldown_hours is None:
        user_cooldown_hours = poke_cfg.get("user_cooldown_hours", 6.0)
    if group_cooldown_seconds is None:
        group_cooldown_seconds = poke_cfg.get("group_cooldown_seconds", 300.0)

    now = time.time()

    # 群级冷却：同群两次戳之间至少间隔 N 秒
    last_group = state.poke_group_last_time.get(group_id, 0)
    if now - last_group < group_cooldown_seconds:
        return False

    # 用户级冷却：同一个群友至少隔 N 小时
    key = _poke_key(group_id, user_qq)
    last_user = state.poke_user_last_time.get(key, 0)
    if (now - last_user) / 3600 < user_cooldown_hours:
        return False

    # 概率拦截
    if random.random() > probability:
        return False

    # LLBot supports send_poke; retain a CQ fallback for other OneBot bridges.
    try:
        bot = get_bot()
        try:
            await bot.call_api(
                "send_poke",
                user_id=int(user_qq),
                group_id=int(group_id),
            )
        except Exception:
            await bot.send_group_msg(
                group_id=int(group_id),
                message=f"[CQ:poke,qq={user_qq}]",
            )
        state.poke_group_last_time[group_id] = now
        state.poke_user_last_time[key] = now
        logger.info(
            "Topic poke sent: group=%s user=%s topic=%s",
            group_id, user_qq, topic,
        )
    except Exception as e:
        logger.warning("Topic poke failed: %s", e)
        return False

    return True


# ─── 关键词反应 / 弔图语录 ──────────────────────────────────────────────────────


def check_reaction(text: str, group_id: str) -> str | None:
    """
    检测关键词触发梗回复。
    返回回复文本或 None。
    """
    config = get_config()
    reactions_cfg = config.get("reactions", {})
    cooldown_min = reactions_cfg.get("cooldown_minutes", 0)
    triggers: dict[str, str | None] = reactions_cfg.get("triggers", {})

    if not triggers or not text:
        return None

    now = time.time()
    last = state.reaction_last_time.get(group_id, 0)
    if cooldown_min > 0 and (now - last) / 60 < cooldown_min:
        return None

    t = text.lower()
    for keyword, reply in triggers.items():
        if reply is None:
            continue
        if keyword.lower() in t:
            state.reaction_last_time[group_id] = now
            return reply

    return None
