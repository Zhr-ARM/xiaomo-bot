"""小源 QQ 机器人 - 共享运行时状态

跨模块共享的状态变量，避免循环引用。
"""
import asyncio
import time

# 机器人的 QQ 号（启动后设置）
bot_qq_id: str | None = None

# 群最后活跃时间：用于冒泡检测
group_last_active: dict[str, float] = {}

# 群消息时间窗口：用于判断群是否正热闹，避免小源过度插话
group_message_times: dict[str, list[float]] = {}

# 小源在各群的发言时间窗口：用于互动调速
bot_reply_times: dict[str, list[float]] = {}

# 群消息缓存：用于复读检测
group_recent_messages: dict[str, list[str]] = {}

# 群短期文本流：用于主动接话时理解刚刚发生的聊天，不进入长期记忆
group_recent_texts: dict[str, list[dict]] = {}

# 复读计数器: group_id -> {text: count}
repeat_counter: dict[str, dict[str, int]] = {}

# 复读锁：防止重复触发
repeat_lock: dict[str, bool] = {}

# 冒泡最后触发时间
bubble_last_time: dict[str, float] = {}

# 冒泡判断最后尝试时间（即使 AI 拒绝也短暂冷却）
bubble_attempt_last_time: dict[str, float] = {}

# 复读最后触发时间
repeat_last_time: dict[str, float] = {}

# 数据库是否已初始化
db_initialized: bool = False

# 关键词反应冷却时间
reaction_last_time: dict[str, float] = {}

# 主动戳戳冷却：group_id:user_qq -> unix timestamp
# 防止频繁戳同一个群友
poke_user_last_time: dict[str, float] = {}

# 戳戳全局冷却：group_id -> unix timestamp（防刷屏）
poke_group_last_time: dict[str, float] = {}

# 自动戳戳冷却：{target_qq} -> unix timestamp（每人独立冷却）
auto_poke_last_time: dict[str, float] = {}

proactive_join_last_time: dict[str, float] = {}

# 主动接话反馈：根据“发完后是否有人接着聊”轻微调节后续主动概率
proactive_join_feedback: dict[str, dict] = {}

# LLM 并发锁：群级，同一时间只处理一个 LLM 请求
_llm_locks: dict[str, asyncio.Lock] = {}

# 群最近图片缓存: group_id -> [{"url": ..., "file": ..., "user_qq": ..., "time": ...}, ...]
# 用于处理"图片先发、文字后 @小源"的场景
group_recent_images: dict[str, list[dict]] = {}
# 最大缓存图片数（每群）
MAX_CACHED_IMAGES = 20
# 图片缓存有效期（秒）
IMAGE_CACHE_TTL = 300


RECENT_WINDOW_SECONDS = 300.0
RECENT_TEXT_WINDOW_SECONDS = 180.0
RECENT_TEXT_LIMIT = 48


def trim_recent_times(
    bucket: dict[str, list[float]],
    group_id: str,
    *,
    now: float | None = None,
    window_seconds: float = RECENT_WINDOW_SECONDS,
) -> list[float]:
    if now is None:
        now = time.time()
    recent = [t for t in bucket.get(group_id, []) if now - t <= window_seconds]
    bucket[group_id] = recent
    return recent


def record_group_message(group_id: str, *, now: float | None = None) -> None:
    if now is None:
        now = time.time()
    times = trim_recent_times(group_message_times, group_id, now=now)
    times.append(now)
    group_message_times[group_id] = times


def record_bot_reply(group_id: str, *, now: float | None = None) -> None:
    if now is None:
        now = time.time()
    times = trim_recent_times(bot_reply_times, group_id, now=now)
    times.append(now)
    bot_reply_times[group_id] = times


def record_recent_group_text(
    group_id: str,
    *,
    user_qq: str | None,
    nickname: str | None,
    text: str,
    source_message_id: str | None = None,
    mentioned_qqs: list[str] | None = None,
    reply_to_message_id: str | None = None,
    carried_from_previous: bool = False,
    now: float | None = None,
) -> None:
    """Record a short-lived group text snippet for ambient participation."""
    clean = (text or "").strip()
    if not clean:
        return
    if now is None:
        now = time.time()

    recent = [
        item
        for item in group_recent_texts.get(group_id, [])
        if now - float(item.get("time", 0)) <= RECENT_TEXT_WINDOW_SECONDS
    ]
    recent.append(
        {
            "time": now,
            "user_qq": str(user_qq or ""),
            "nickname": (nickname or "").strip(),
            "text": clean[:240],
            "source_message_id": str(source_message_id or ""),
            "mentioned_qqs": [str(qq) for qq in (mentioned_qqs or [])],
            "reply_to_message_id": str(reply_to_message_id or ""),
            "carried_from_previous": bool(carried_from_previous),
        }
    )
    group_recent_texts[group_id] = recent[-RECENT_TEXT_LIMIT:]


def format_recent_group_flow(
    group_id: str,
    *,
    limit: int = 8,
    exclude_source_message_id: str | None = None,
    now: float | None = None,
) -> str:
    """Format recent in-memory group flow for prompt context."""
    if now is None:
        now = time.time()
    recent = [
        item
        for item in group_recent_texts.get(group_id, [])
        if now - float(item.get("time", 0)) <= RECENT_TEXT_WINDOW_SECONDS
    ]
    group_recent_texts[group_id] = recent[-RECENT_TEXT_LIMIT:]
    if not recent:
        return ""

    lines = []
    visible = [
        item
        for item in recent
        if not exclude_source_message_id
        or str(item.get("source_message_id") or "")
        != str(exclude_source_message_id)
    ]
    for item in visible[-max(1, int(limit)):]:
        name = item.get("nickname") or (
            f"QQ{item.get('user_qq')}" if item.get("user_qq") else "成员"
        )
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"[{name}]: {text[:160]}")
    return "\n".join(lines)


def find_recent_text_by_user(
    group_id: str,
    *,
    user_qq: str,
    exclude_source_message_id: str | None = None,
    max_age_seconds: float = 75.0,
    now: float | None = None,
) -> dict | None:
    """Find the sender's previous text for a split "message then @bot" turn."""

    if now is None:
        now = time.time()
    for item in reversed(group_recent_texts.get(group_id, [])):
        if now - float(item.get("time", 0)) > max_age_seconds:
            continue
        if str(item.get("user_qq") or "") != str(user_qq):
            continue
        if (
            exclude_source_message_id
            and str(item.get("source_message_id") or "")
            == str(exclude_source_message_id)
        ):
            continue
        if item.get("carried_from_previous"):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            return item
    return None


def mark_proactive_join_sent(
    group_id: str,
    *,
    source_message_id: str | None = None,
    now: float | None = None,
) -> None:
    """Start a feedback window after an ambient proactive reply is sent."""
    if now is None:
        now = time.time()
    proactive_join_last_time[group_id] = now
    data = proactive_join_feedback.setdefault(group_id, {"score": 0.0})
    data["pending"] = {
        "sent_at": now,
        "expires_at": now + 180.0,
        "source_message_id": str(source_message_id or ""),
    }


def observe_proactive_join_feedback(
    group_id: str,
    *,
    user_qq: str | None = None,
    bot_qq: str | None = None,
    text: str = "",
    mentions_bot: bool = False,
    reply_to_message_id: str | None = None,
    now: float | None = None,
) -> str | None:
    """Update feedback score when the next human message arrives."""
    if user_qq and bot_qq and str(user_qq) == str(bot_qq):
        return None
    if now is None:
        now = time.time()

    data = proactive_join_feedback.get(group_id)
    if not data:
        return None
    pending = data.get("pending")
    if not pending:
        return None

    score = float(data.get("score", 0.0))
    pending_source = str(pending.get("source_message_id") or "")
    direct_reply = bool(
        pending_source
        and reply_to_message_id
        and pending_source == str(reply_to_message_id)
    )
    normalized = (text or "").strip().lower()
    conversational_signal = any(
        cue in normalized
        for cue in ("?", "？", "哈哈", "确实", "对", "不是", "怎么", "为啥", "谢谢")
    )
    if now <= float(pending.get("expires_at", 0)) and (
        mentions_bot or direct_reply or conversational_signal
    ):
        score = min(0.35, score + 0.08)
        outcome = "continued"
    elif now > float(pending.get("expires_at", 0)):
        score = max(-0.35, score - 0.05)
        outcome = "stalled"
    else:
        return "neutral"

    data["score"] = score
    data["last_outcome"] = outcome
    data["last_updated"] = now
    data.pop("pending", None)
    return outcome


def proactive_join_probability_multiplier(group_id: str) -> float:
    """Return a small adaptive multiplier based on recent proactive outcomes."""
    score = float(proactive_join_feedback.get(group_id, {}).get("score", 0.0))
    return max(0.65, min(1.35, 1.0 + score))


def get_llm_lock(group_id: str) -> asyncio.Lock:
    if group_id not in _llm_locks:
        _llm_locks[group_id] = asyncio.Lock()
    return _llm_locks[group_id]


# ─── 情绪状态追踪：跨轮次保持角色连贯 ─────────────────────────────────────────────

# 群级情绪状态: group_id -> {"mood": str, "strength": float, "updated_at": float}
# mood: "snarky" | "playful" | "gentle" | "energetic" | "elegant" | "cute"
group_moods: dict[str, dict] = {}

# 情绪衰减半衰期（秒）：10 分钟后情绪强度减半
MOOD_DECAY_HALF_LIFE: float = 600.0


def get_group_mood(group_id: str) -> dict | None:
    """获取群当前情绪状态（已衰减）"""
    mood_data = group_moods.get(group_id)
    if not mood_data:
        return None
    age = time.time() - mood_data["updated_at"]
    decay = 0.5 ** (age / MOOD_DECAY_HALF_LIFE)
    strength = mood_data["strength"] * decay
    if strength < 0.15:
        group_moods.pop(group_id, None)
        return None
    return {"mood": mood_data["mood"], "strength": round(strength, 3)}


def update_group_mood(group_id: str, mood: str, strength: float = 1.0):
    """更新群情绪状态"""
    group_moods[group_id] = {
        "mood": mood,
        "strength": min(strength, 1.0),
        "updated_at": time.time(),
    }
