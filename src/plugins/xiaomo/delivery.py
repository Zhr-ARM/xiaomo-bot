"""Reliable outbound delivery for group text messages."""

from __future__ import annotations

import logging
from typing import Any

from nonebot.adapters.onebot.v11 import Bot

from . import state

logger = logging.getLogger("xiaomo.delivery")


def _source_message_id(result: Any) -> str | None:
    if isinstance(result, dict):
        value = result.get("message_id")
        return str(value) if value is not None else None
    value = getattr(result, "message_id", None)
    return str(value) if value is not None else None


async def send_group_text(
    bot: Bot,
    group_id: str,
    content: str,
    *,
    remember: bool = True,
) -> str | None:
    """Send first, then update local state and memory on confirmed success."""

    clean = (content or "").strip()
    if not clean:
        raise ValueError("group text cannot be empty")

    result = await bot.send_group_msg(group_id=int(group_id), message=clean)
    state.record_bot_reply(group_id)
    from .runtime_state import schedule_persist

    schedule_persist()

    if remember:
        try:
            from .memory import store_memory

            stored_message_id = await store_memory(
                user_qq=None,
                group_id=group_id,
                scene="group",
                role="assistant",
                content=clean,
            )
            source_message_id = _source_message_id(result)
            if source_message_id:
                from .database import (
                    get_session,
                    link_source_message_id,
                )

                async with await get_session() as session:
                    await link_source_message_id(
                        session,
                        group_id=group_id,
                        source_message_id=source_message_id,
                        message_id=stored_message_id,
                    )
                    await session.commit()
        except Exception:
            # Delivery already succeeded. Memory failure must not trigger a retry,
            # which could post the same visible message twice.
            logger.exception(
                "Sent group message but failed to persist it: group=%s", group_id
            )

    return _source_message_id(result)
