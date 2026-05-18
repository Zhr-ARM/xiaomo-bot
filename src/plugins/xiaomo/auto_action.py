"""小源 QQ 机器人 - 自动行为模块

- 自动冒泡：群长时间无消息时主动卖萌
- 复读检测：检测连续相同消息并选择性复读
- 主动接话：检测有趣话题
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from nonebot import get_bot

from . import state
from .config import get_config

logger = logging.getLogger("xiaomo.auto_action")

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


# ─── 后台冒泡循环 ─────────────────────────────────────────────────────────────


async def start_bubble_loop():
    global _bubble_task

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
                inactive_for = (now - state.group_last_active.get(gid, now)) / 60

                if inactive_for < inactive_min:
                    continue

                last_bubble = state.bubble_last_time.get(gid, 0)
                if (now - last_bubble) / 60 < cooldown_min:
                    continue

                if random.random() < 0.5:
                    continue

                quote = random.choice(BUBBLE_QUOTES)
                try:
                    await bot.send_group_msg(group_id=int(gid), message=quote)
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
    "吃饭", "好吃", "美食", "奶茶",
    "游戏", "动漫", "追番", "二次元",
    "摸鱼", "划水", "摸鱼人",
    "熬夜", "失眠", "睡不着",
]


def detect_interesting_topic(text: str) -> str | None:
    for topic in INTEREST_TOPICS:
        if topic in text:
            return topic
    return None


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
