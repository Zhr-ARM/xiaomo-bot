"""小源 QQ 机器人 - 静默窗口定时器

收到消息不立即回复，等待用户连续发言结束后再生成回复。
私聊窗口期短（~3s），群聊窗口期长（~5s）。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Awaitable

from .config import get_config

logger = logging.getLogger("xiaomo.window")

# (key, messages) → None
ReplyCallback = Callable[[str, list[dict]], Awaitable[None]]


class SilentWindow:
    """静默窗口管理器"""

    def __init__(self):
        config = get_config().get("silent_window", {})
        self._private_seconds: float = config.get("private_seconds", 3)
        self._group_seconds: float = config.get("group_seconds", 5)
        self._timers: dict[str, asyncio.Task] = {}
        self._pending: dict[str, list[dict]] = defaultdict(list)
        self._callback: ReplyCallback | None = None

    def set_callback(self, callback: ReplyCallback):
        """设置窗口到期时的回调"""
        self._callback = callback

    def enqueue(self, key: str, message: dict, is_group: bool = False):
        """
        将消息加入等待队列并重置计时器。
        key: conversation key (如 private:123456 或 group:789012)
        """
        self._pending[key].append(message)

        # 取消旧计时器
        if key in self._timers and not self._timers[key].done():
            self._timers[key].cancel()

        # 启动新计时器
        wait_seconds = self._group_seconds if is_group else self._private_seconds
        self._timers[key] = asyncio.create_task(self._wait_then_fire(key, wait_seconds))

    async def _wait_then_fire(self, key: str, seconds: float):
        """等待窗口到期，然后触发回调"""
        try:
            await asyncio.sleep(seconds)
            messages = self._pending.pop(key, [])
            self._timers.pop(key, None)

            if messages and self._callback:
                await self._callback(key, messages)
        except asyncio.CancelledError:
            pass  # 被新消息重置，正常行为
        except Exception:
            logger.exception("SilentWindow callback failed for key %s", key)

    def flush(self, key: str):
        """立即触发某个 key 的待处理消息（不等待窗口）"""
        timer = self._timers.pop(key, None)
        if timer and not timer.done():
            timer.cancel()
        messages = self._pending.pop(key, [])
        return messages


# 全局单例
_silent_window: SilentWindow | None = None


def get_silent_window() -> SilentWindow:
    global _silent_window
    if _silent_window is None:
        _silent_window = SilentWindow()
    return _silent_window
