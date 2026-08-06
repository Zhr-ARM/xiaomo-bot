"""Debounced persistence for transient social state."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import state
from .database import RuntimeState, get_session

logger = logging.getLogger("xiaomo.runtime_state")

_SNAPSHOT_KEY = "social-v1"
_task: asyncio.Task | None = None
_FIELDS = (
    "group_last_active",
    "group_message_times",
    "bot_reply_times",
    "group_recent_bot_texts",
    "group_recent_texts",
    "group_dialogue_sessions",
    "bubble_last_time",
    "bubble_attempt_last_time",
    "repeat_last_time",
    "reaction_last_time",
    "poke_user_last_time",
    "poke_group_last_time",
    "auto_poke_last_time",
    "proactive_join_last_time",
    "proactive_join_feedback",
    "group_moods",
)


def _snapshot() -> dict:
    return {name: getattr(state, name) for name in _FIELDS}


async def persist_now() -> None:
    payload = json.dumps(_snapshot(), ensure_ascii=False, separators=(",", ":"))
    async with await get_session() as session:
        await session.execute(
            sqlite_insert(RuntimeState)
            .values(key=_SNAPSHOT_KEY, value_json=payload, updated_at=time.time())
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value_json": payload, "updated_at": time.time()},
            )
        )
        await session.commit()


async def _persist_after_delay(delay: float) -> None:
    global _task
    try:
        await asyncio.sleep(delay)
        await persist_now()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to persist runtime state")
    finally:
        _task = None


def schedule_persist(delay: float = 1.0) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(
            _persist_after_delay(delay),
            name="xiaomo-runtime-state-save",
        )


async def restore() -> None:
    async with await get_session() as session:
        result = await session.execute(
            select(RuntimeState).where(RuntimeState.key == _SNAPSHOT_KEY)
        )
        row = result.scalar_one_or_none()
    if row is None:
        return
    try:
        payload = json.loads(row.value_json)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Ignoring invalid runtime state snapshot")
        return
    for name in _FIELDS:
        value = payload.get(name)
        target = getattr(state, name)
        if isinstance(value, dict) and isinstance(target, dict):
            target.clear()
            target.update(value)
    logger.info("Runtime social state restored")


async def shutdown() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    await persist_now()
