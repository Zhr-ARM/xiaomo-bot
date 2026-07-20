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
