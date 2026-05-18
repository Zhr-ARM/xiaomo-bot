"""小源 QQ 机器人 - 消息处理器

开源协会顾问猫娘 — 仅限群聊，不接受私聊。
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time as _time

from nonebot import on_message, on_notice, get_bot
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, NoticeEvent

from . import state
from .config import get_config
from sqlalchemy import or_, select

from .database import (
    Nickname,
    User,
    UserNickname,
    init_database,
    get_or_create_user,
    get_session,
    get_user_profile_summary,
)
from .llm import get_llm
from .memory import (
    build_context,
    compress_old_memories,
    group_key,
    store_memory,
)
from .window import get_silent_window
from .auto_action import check_reaction, check_repeat, detect_interesting_topic
from .weather import query_weather

logger = logging.getLogger("xiaomo.handlers")

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
    "外面冷", "外面热", "外面",
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
    if any(kw in t for kw in ["来个笑话", "冷笑话", "讲个笑话", "来个嵌入式笑话"]):
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


async def _ensure_init():
    if not state.db_initialized:
        await init_database()
        state.db_initialized = True


def _is_allowed_group(group_id: str) -> bool:
    allowed = get_config().get("allowed_group_ids", [])
    if not allowed:
        return True
    return group_id in allowed


# ─── 消息预处理 ────────────────────────────────────────────────────────────────


def _extract_text(event: MessageEvent) -> str:
    text = event.get_plaintext().strip()
    text = re.sub(r"\[CQ:[^\]]+\]", "", text).strip()
    return text


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
    for name in ["小源"]:
        if name in text:
            return True
    return False


def _is_text_at_mention(event: GroupMessageEvent) -> bool:
    """LLBot sends @ as plain text, not CQ at segment."""
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "").strip()
            if re.search(r"@\S+", t):
                return True
    return False


def _extract_at_text(event: GroupMessageEvent, bot_qq: str) -> str:
    text = ""
    for seg in event.message:
        if seg.type == "text":
            t = seg.data.get("text", "")
            t = re.sub(r"@\S+\s*", "", t)
            text += t
        elif seg.type == "at":
            if seg.data.get("qq") != bot_qq:
                text += f"@{seg.data.get('qq', 'unknown')}"
    return text.strip()


async def _update_user_profile(qq_id: str, nickname: str | None = None):
    async with await get_session() as session:
        user = await get_or_create_user(session, qq_id)
        if nickname and not user.nickname:
            user.nickname = nickname
        await session.commit()


# ─── 回复发送 ──────────────────────────────────────────────────────────────────


async def _send_group_reply(bot: Bot, group_id: str, content: str):
    preview = content[:80].encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    print(f"[REPLY] group={group_id} len={len(content)}: {preview}")
    await store_memory(
        user_qq=None, group_id=group_id, scene="group",
        role="assistant", content=content,
    )
    await bot.send_group_msg(group_id=int(group_id), message=content)


# ─── 核心处理逻辑 ──────────────────────────────────────────────────────────────


async def _process_messages(key: str, messages: list[dict]):
    """静默窗口到期后，处理一批消息并生成回复"""
    if not messages:
        return

    try:
        bot = get_bot()
    except Exception:
        logger.error("Failed to get bot instance")
        return

    first_msg = messages[0]
    group_id = first_msg.get("group_id")
    user_qq = first_msg.get("user_qq")
    mode = first_msg.get("mode", "normal")
    mode_target = first_msg.get("mode_target", "")
    if not group_id:
        return

    # Fetch user profile and build identity prefix
    profile = {}
    user_display = f"QQ{user_qq}" if user_qq else "未知成员"
    if user_qq:
        async with await get_session() as session:
            profile = await get_user_profile_summary(session, user_qq)
        nick = profile.get("nickname", "")
        nicks = profile.get("nicknames", [])
        pref = profile.get("profile", {}).get("preferred_name", "")
        if pref:
            user_display = pref
        elif nick:
            user_display = nick
        elif nicks:
            user_display = nicks[0]

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
                # 可能传来的是名字不是QQ号，试着按昵称查
                result = await session.execute(
                    select(UserNickname).where(
                        UserNickname.nickname_id == select(Nickname.id).where(
                            Nickname.name == mode_target
                        ).scalar_subquery(),
                    ).limit(1)
                )
                un_result = result.scalar_one_or_none()
                if un_result:
                    target_profile = await get_user_profile_summary(session, un_result.user_qq)

    raw_text = " ".join(m["text"] for m in messages if m["text"])

    # 构建用户消息
    if mode == "praise":
        target_name = target_profile.get("nickname", mode_target) if target_profile else mode_target
        combined_text = f"[{user_display} (QQ:{user_qq})]: 请夸夸 {target_name}。下面是 {target_name} 的信息，请据此写夸赞。"
    elif mode == "roast":
        target_name = target_profile.get("nickname", mode_target) if target_profile else mode_target
        combined_text = f"[{user_display} (QQ:{user_qq})]: 请点草 {target_name}。下面是 {target_name} 的信息，请据此写友好吐槽。"
    elif mode == "joke":
        combined_text = f"[{user_display} (QQ:{user_qq})]: 来个嵌入式冷笑话"
    else:
        combined_text = f"[{user_display} (QQ:{user_qq})]: {raw_text}"

    context, meta = await build_context(
        scene="group",
        user_qq=user_qq,
        group_id=group_id,
        half_life_minutes=get_config().get("memory", {}).get("weight_half_life_minutes", 60),
        max_tokens=get_config().get("memory", {}).get("max_context_tokens", 8000),
    )


    # 夸夸/点草模式：附上目标用户画像
    llm_profile = profile
    if target_profile and target_profile.get("exists"):
        combined_text += f"\n\n[目标成员信息]\n昵称: {target_profile.get('nickname', '未知')}\n"
        combined_text += f"称呼: {', '.join(target_profile.get('nicknames', []))}\n"
        combined_text += f"互动消息数: {target_profile.get('total_messages', 0)}\n"
        pdata = target_profile.get('profile', {})
        if pdata.get('topics'):
            combined_text += f"技术方向: {', '.join(pdata['topics'])}\n"
        llm_profile = target_profile  # 用目标用户的画像驱动回复

    # 群级并发锁：同一时间只处理一个 LLM 请求
    llm_lock: asyncio.Lock = state.get_llm_lock(group_id)
    reply = ""
    try:
        async with asyncio.timeout(45):
            async with llm_lock:
                print(f"[LLM] Starting chat: mode={mode} group={group_id}")
                llm = get_llm()
                try:
                    reply = await llm.chat(
                        context=context,
                        user_profile=llm_profile,
                        scene="group",
                        user_message=combined_text,
                        mode=mode,
                    )
                    print(f"[LLM] Reply received: len={len(reply)}")
                except Exception as e:
                    logger.exception("LLM call failed")
                    reply = "喵呜...小源的脑子好像卡住了 (´;ω;`) 等下再试试好嘛？"
                    print(f"[LLM] Exception: {e}")
    except TimeoutError:
        logger.warning("LLM lock timeout for group %s, skipping", group_id)
        print(f"[LLM] Lock timeout for group={group_id}")
        return

    print(f"[REPLY] Attempting to send: len={len(reply)}")
    try:
        await _send_group_reply(bot, group_id, reply)
        print(f"[REPLY] Sent successfully")
    except Exception as e:
        print(f"[REPLY] Failed to send: {e}")
        import traceback
        traceback.print_exc()

    threshold = get_config().get("memory", {}).get("compress_threshold_tokens", 15000)
    await compress_old_memories("group", user_qq, group_id, threshold)


# ─── 消息接收 ──────────────────────────────────────────────────────────────────


async def _on_message(event: MessageEvent):
    await _ensure_init()

    bot = get_bot()
    if state.bot_qq_id is None:
        state.bot_qq_id = str(bot.self_id)

    text = _extract_text(event)
    user_qq = str(event.user_id)
    sender = event.sender
    nickname = sender.nickname if sender else None

    await _update_user_profile(user_qq, nickname)

    # 私聊：直接拒绝
    if isinstance(event, PrivateMessageEvent):
        return

    # 群聊处理
    if isinstance(event, GroupMessageEvent):
        group_id_str = str(event.group_id)
        state.group_last_active[group_id_str] = _time.time()

        if not _is_allowed_group(group_id_str):
            return

        mentions_bot = _is_mentioned(event, state.bot_qq_id)
        called_bot = _is_called(text)
        at_text = _extract_at_text(event, state.bot_qq_id)
        text_at = _is_text_at_mention(event)
        to_me = getattr(event, "to_me", False)
        is_empty = not text or not text.strip()

        should_respond = mentions_bot or called_bot or text_at or to_me

        # ── 天气查询：获取数据注入 LLM，不走死板回复 ──
        weather_data = None
        weather_text = (text or at_text) if should_respond else None
        if weather_text:
            weather_q = _is_weather_query(weather_text)
            if weather_q:
                weather_data = await query_weather(weather_q)

        if should_respond:
            effective_text = at_text if at_text else "[有成员@了小源]"

            # 检测特殊指令模式
            check_text = at_text if (mentions_bot or text_at or to_me) else text
            special_mode, special_target, _ = _detect_special_mode(check_text or "")

            if special_mode != "normal":
                effective_text = f"[指令模式: {special_mode}] 目标: {special_target or '无'}"
                if special_mode == "joke":
                    effective_text = "[指令模式: joke] 讲一个嵌入式冷笑话"

            # 将天气数据附加到消息中，让 LLM 自然融入回复
            if weather_data:
                effective_text = f"{effective_text}\n\n[系统注：当前成都天气数据如下，请根据天气自然回答，不要生硬列出数据]\n{weather_data}"

            await store_memory(
                user_qq=user_qq, group_id=group_id_str, scene="group",
                role="user", content=effective_text,
            )

            silent = get_silent_window()
            silent.enqueue(
                group_key(group_id_str),
                {
                    "scene": "group", "target_id": user_qq,
                    "group_id": group_id_str, "user_qq": user_qq,
                    "text": effective_text,
                    "timestamp": _time.time(),
                    "mode": special_mode,
                    "mode_target": special_target,
                },
                is_group=True,
            )

        # 复读检测
        if text:
            repeat_text = await check_repeat(group_id_str, text)
            if repeat_text:
                await bot.send_group_msg(group_id=int(group_id_str), message=repeat_text)

        # 主动接话
        if not should_respond and text:
            topic = detect_interesting_topic(text)
            if topic and random.random() < 0.05:
                await asyncio.sleep(2)
                await bot.send_group_msg(
                    group_id=int(group_id_str),
                    message=f"诶！有人提到{topic}？喵～ 小源对这个也很感兴趣呢 (=^･ω･^=)",
                )

        # 关键词反应 / 弔图语录
        if not should_respond and text:
            reaction = check_reaction(text, group_id_str)
            if reaction:
                await asyncio.sleep(1)
                await bot.send_group_msg(group_id=int(group_id_str), message=reaction)


# ─── 事件注册 ──────────────────────────────────────────────────────────────────

msg_handler = on_message(priority=10, block=False)


@msg_handler.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    await _on_message(event)


# ─── 戳一戳 ──────────────────────────────────────────────────────────────────

_POKE_REPLIES = [
    "喵呜！不要戳小源啦 (´;ω;`)",
    "哎呀！再戳小源要生气了哦！(｀・ω・´)",
    "别戳了别戳了，毛都被你戳乱了喵...",
    "呜哇！说了不要戳！(ﾉ>ω<)ﾉ",
    "戳一下掉一根猫毛，你要负责喵！",
    "Σ(ﾟДﾟ) 是谁在戳小源！",
]

poke_handler = on_notice(priority=10, block=False)


@poke_handler.handle()
async def handle_notice(bot: Bot, event: NoticeEvent):
    nt = getattr(event, "notice_type", "")
    st = getattr(event, "sub_type", "")
    target = getattr(event, "target_id", None)
    gid = getattr(event, "group_id", None)
    if nt != "notify" or st != "poke":
        return
    if str(target) != str(bot.self_id):
        return
    if gid:
        import random as _random
        reply = _random.choice(_POKE_REPLIES)
        await bot.send_group_msg(group_id=gid, message=reply)


def setup_silent_callback():
    get_silent_window().set_callback(_process_messages)
