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

# 复读计数器: group_id -> {text: count}
repeat_counter: dict[str, dict[str, int]] = {}

# 复读锁：防止重复触发
repeat_lock: dict[str, bool] = {}

# 冒泡最后触发时间
bubble_last_time: dict[str, float] = {}

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
