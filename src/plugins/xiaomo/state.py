"""小源 QQ 机器人 - 共享运行时状态

跨模块共享的状态变量，避免循环引用。
"""
import asyncio
import time

# 机器人的 QQ 号（启动后设置）
bot_qq_id: str | None = None

# 群最后活跃时间：用于冒泡检测
group_last_active: dict[str, float] = {}

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

# LLM 并发锁：群级，同一时间只处理一个 LLM 请求
_llm_locks: dict[str, asyncio.Lock] = {}


def get_llm_lock(group_id: str) -> asyncio.Lock:
    if group_id not in _llm_locks:
        _llm_locks[group_id] = asyncio.Lock()
    return _llm_locks[group_id]
