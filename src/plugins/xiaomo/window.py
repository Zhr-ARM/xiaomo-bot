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
    """静默窗口管理器

    两阶段设计：
    - WAITING: 计时器倒计时，新消息会重置计时器（合批）
    - PROCESSING: 新消息进入缓冲；显式请求可取消正在生成的主动插话
    """

    def __init__(self):
        config = get_config().get("silent_window", {})
        self._private_seconds: float = config.get("private_seconds", 3)
        self._group_seconds: float = config.get("group_seconds", 5)
        self._max_pending_per_key: int = max(
            2, int(config.get("max_pending_per_key", 24))
        )
        self._max_pending_total: int = max(
            self._max_pending_per_key,
            int(config.get("max_pending_total", 240)),
        )
        self._timers: dict[str, asyncio.Task] = {}
        self._pending: dict[str, list[dict]] = defaultdict(list)
        self._callback: ReplyCallback | None = None
        self._closed = False
        # 正在处理中的 key 集合；显式消息可抢占主动插话，其余消息只缓冲。
        self._processing: set[str] = set()
        self._processing_tasks: dict[str, asyncio.Task] = {}
        self._processing_solicited: dict[str, bool] = {}
        # 记录每个 key 是群聊还是私聊，处理完重启计时器时用
        self._is_group: dict[str, bool] = {}
        # 每个 key 的本轮等待时长；显式 @ 可以使用更短窗口，普通群聊仍用默认窗口。
        self._wait_seconds: dict[str, float] = {}

    def set_callback(self, callback: ReplyCallback):
        """设置窗口到期时的回调"""
        self._callback = callback

    def enqueue(
        self,
        key: str,
        message: dict,
        is_group: bool = False,
        wait_seconds: float | None = None,
    ):
        """
        将消息加入等待队列并重置计时器。
        key: conversation key (如 private:123456 或 group:789012)

        如果回调正在处理中，普通消息只缓冲；显式请求可抢占主动插话。
        """
        if self._closed:
            logger.warning("SilentWindow ignored message after shutdown: %s", key)
            return
        self._pending[key].append(message)
        self._enforce_limits(key)
        self._is_group[key] = is_group
        default_wait = self._group_seconds if is_group else self._private_seconds
        self._wait_seconds[key] = default_wait if wait_seconds is None else max(0.0, float(wait_seconds))

        # A direct mention should not wait behind an in-flight ambient interjection.
        if key in self._processing:
            incoming_solicited = bool(
                message.get("explicit_trigger") or message.get("dialogue_followup")
            )
            current_solicited = self._processing_solicited.get(key, False)
            task = self._processing_tasks.get(key)
            if (
                incoming_solicited
                and not current_solicited
                and task is not None
                and not task.done()
            ):
                logger.info("Solicited turn preempted ambient processing for %s", key)
                task.cancel()
            return

        # 取消等待中的旧计时器（还没进入 processing 阶段）
        if key in self._timers and not self._timers[key].done():
            self._timers[key].cancel()

        # 启动新计时器
        self._timers[key] = asyncio.create_task(
            self._wait_then_fire(key, self._wait_seconds[key])
        )

    @staticmethod
    def _message_priority(index: int, message: dict) -> tuple[int, float, int]:
        try:
            timestamp = float(message.get("timestamp") or 0)
        except (TypeError, ValueError):
            timestamp = 0.0
        solicited = message.get("explicit_trigger") or message.get("dialogue_followup")
        return (1 if solicited else 0, timestamp, index)

    def _enforce_limits(self, changed_key: str) -> None:
        bucket = self._pending[changed_key]
        if len(bucket) > self._max_pending_per_key:
            keep_indices = sorted(
                sorted(
                    range(len(bucket)),
                    key=lambda idx: self._message_priority(idx, bucket[idx]),
                    reverse=True,
                )[: self._max_pending_per_key]
            )
            dropped = len(bucket) - len(keep_indices)
            self._pending[changed_key] = [bucket[idx] for idx in keep_indices]
            logger.warning(
                "SilentWindow dropped %d queued messages for %s", dropped, changed_key
            )

        while sum(len(messages) for messages in self._pending.values()) > self._max_pending_total:
            candidates = []
            for key, messages in self._pending.items():
                for index, queued in enumerate(messages):
                    candidates.append(
                        (
                            1
                            if queued.get("explicit_trigger")
                            or queued.get("dialogue_followup")
                            else 0,
                            float(queued.get("timestamp") or 0),
                            key,
                            index,
                        )
                    )
            if not candidates:
                break
            _, _, key, index = min(candidates)
            self._pending[key].pop(index)
            if not self._pending[key]:
                self._pending.pop(key, None)
            logger.warning("SilentWindow global queue cap dropped a message for %s", key)

    async def _wait_then_fire(self, key: str, seconds: float):
        """等待窗口到期，然后触发回调"""
        try:
            await asyncio.sleep(seconds)

            # 进入处理阶段 — 新消息不会再取消我们
            self._timers.pop(key, None)
            self._processing.add(key)
            current_task = asyncio.current_task()
            if current_task is not None:
                self._processing_tasks[key] = current_task

            messages = self._pending.pop(key, [])
            self._processing_solicited[key] = any(
                message.get("explicit_trigger") or message.get("dialogue_followup")
                for message in messages
            )

            if messages and self._callback:
                await self._callback(key, messages)

        except asyncio.CancelledError:
            pass  # 被新消息重置（仅在 WAITING 阶段），正常行为
        except Exception:
            logger.exception("SilentWindow callback failed for key %s", key)
        finally:
            self._processing.discard(key)
            self._processing_tasks.pop(key, None)
            self._processing_solicited.pop(key, None)

            # 处理期间有新消息到达 → 重启计时器，不让它们被遗忘
            if not self._closed and key in self._pending and self._pending[key]:
                is_grp = self._is_group.get(key, False)
                wait = self._wait_seconds.get(
                    key,
                    self._group_seconds if is_grp else self._private_seconds,
                )
                self._timers[key] = asyncio.create_task(
                    self._wait_then_fire(key, wait)
                )
            else:
                self._wait_seconds.pop(key, None)

    def flush(self, key: str):
        """立即触发某个 key 的待处理消息（不等待窗口）"""
        # 如果在处理中，只返回 pending 不清除（等处理完会自动处理）
        if key in self._processing:
            return list(self._pending.get(key, []))
        timer = self._timers.pop(key, None)
        if timer and not timer.done():
            timer.cancel()
        messages = self._pending.pop(key, [])
        return messages

    async def close(self) -> None:
        """Cancel waiting and in-flight callbacks during application shutdown."""

        self._closed = True
        tasks = set(self._timers.values()) | set(self._processing_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._timers.clear()
        self._processing_tasks.clear()
        self._processing.clear()
        self._processing_solicited.clear()
        self._pending.clear()
        self._wait_seconds.clear()


# 全局单例
_silent_window: SilentWindow | None = None


def get_silent_window() -> SilentWindow:
    global _silent_window
    if _silent_window is None or _silent_window._closed:
        _silent_window = SilentWindow()
    return _silent_window
