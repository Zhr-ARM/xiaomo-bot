from __future__ import annotations

import asyncio

import pytest

from src.plugins.xiaomo.window import SilentWindow


@pytest.mark.asyncio
async def test_enqueue_can_override_wait_seconds_for_fast_explicit_replies():
    window = SilentWindow()
    fired = []

    async def callback(key, messages):
        fired.append((key, messages))

    window.set_callback(callback)
    window.enqueue(
        "group:g1",
        {"text": "hello"},
        is_group=True,
        wait_seconds=0.01,
    )

    await asyncio.sleep(0.06)

    assert fired == [("group:g1", [{"text": "hello"}])]


@pytest.mark.asyncio
async def test_pending_queue_caps_keep_recent_explicit_messages(monkeypatch):
    monkeypatch.setattr(
        "src.plugins.xiaomo.window.get_config",
        lambda: {
            "silent_window": {
                "group_seconds": 60,
                "max_pending_per_key": 3,
                "max_pending_total": 4,
            }
        },
    )
    window = SilentWindow()

    for index in range(6):
        window.enqueue(
            "group:g1",
            {
                "text": str(index),
                "timestamp": index,
                "explicit_trigger": index == 1,
            },
            is_group=True,
        )
    window.enqueue(
        "group:g2",
        {"text": "other", "timestamp": 7, "explicit_trigger": True},
        is_group=True,
    )
    window.enqueue(
        "group:g3",
        {"text": "latest", "timestamp": 8, "explicit_trigger": False},
        is_group=True,
    )

    queued = [message for messages in window._pending.values() for message in messages]
    assert len(queued) <= 4
    assert any(message["text"] == "1" for message in queued)
    assert any(message["text"] == "other" for message in queued)

    for key in list(window._pending):
        window.flush(key)
