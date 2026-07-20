"""小源机器人模块 smoke test。

这个脚本只验证本地模块协作，不连接 QQ，不调用外部 LLM，不触碰真实数据库。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import nonebot

_tmpdir = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_tmpdir.name) / "xiaomo-test.db")

nonebot.init()

from src.plugins.xiaomo.database import close_database, init_database
from src.plugins.xiaomo.memory import (
    build_context,
    calc_weight,
    compress_old_memories,
    store_memory,
)
from src.plugins.xiaomo.window import SilentWindow
from src.plugins.xiaomo.auto_action import BUBBLE_QUOTES, check_repeat, detect_interesting_topic
from src.plugins.xiaomo.filter_utils import check_content_safe, extract_code_blocks


async def run_all():
    await init_database()

    try:
        group_id = "test-group"
        user_qq = "111"

        print("--- Test 1: Group Conversation Storage ---")
        await store_memory(user_qq, group_id, "group", "user", "hello")
        await store_memory(None, group_id, "group", "assistant", "hi there")
        await store_memory(user_qq, group_id, "group", "user", "how are you")
        await store_memory(None, group_id, "group", "assistant", "great!")
        print("  OK - 4 group messages stored")

        print("--- Test 2: Context Building ---")
        ctx, meta, structured_history = await build_context(
            "group", user_qq=user_qq, group_id=group_id,
        )
        assert meta["message_count"] >= 2
        assert meta["profile"]["exists"]
        assert structured_history
        assert "hello" in ctx
        print(f"  OK - {meta['message_count']} messages in context")

        print("--- Test 3: Weight Decay ---")
        now = time.time()
        w0 = calc_weight(now, half_life_minutes=60)
        w1 = calc_weight(now - 3600, half_life_minutes=60)
        w2 = calc_weight(now - 7200, half_life_minutes=60)
        assert 0.49 < w1 < 0.51, f"1h weight should be ~0.5, got {w1}"
        assert 0.24 < w2 < 0.26, f"2h weight should be ~0.25, got {w2}"
        print(f"  OK - weights: now={w0:.3f} 1h={w1:.3f} 2h={w2:.3f}")

        print("--- Test 4: Silent Window ---")
        window = SilentWindow()
        window._private_seconds = 0.1
        fired = []

        async def cb(key, msgs):
            fired.append((key, msgs))

        window.set_callback(cb)
        window.enqueue("test_key", {"text": "hello"}, is_group=False)
        await asyncio.sleep(0.2)
        assert len(fired) == 1, "should fire after window"
        print("  OK - fired after delay")

        print("--- Test 5: Repeat Detection ---")
        from src.plugins.xiaomo import state

        state.repeat_counter.clear()
        state.repeat_lock.clear()
        state.repeat_last_time.clear()
        await check_repeat("g1", "haha")
        await check_repeat("g1", "haha")
        repeated = await check_repeat("g1", "haha")
        assert repeated == "haha", f"should detect repeat, got {repeated}"
        print("  OK - repeat detected:", repeated)

        print("--- Test 6: Topic Detection ---")
        topic = detect_interesting_topic("bug")
        assert topic == "bug"
        assert detect_interesting_topic("normal chat") is None
        print("  OK - topic detection works")

        print("--- Test 7: Content Filter ---")
        ok, _ = check_content_safe("hello world")
        assert ok
        print("  OK - safe content passes")

        print("--- Test 8: Code Extraction ---")
        blocks = extract_code_blocks("```python\nprint(1)\n```")
        assert len(blocks) == 1
        assert blocks[0]["language"] == "python"
        print("  OK - code block extracted")

        print("--- Test 9: Memory Compression No-op ---")
        await compress_old_memories("group", user_qq=user_qq, group_id=group_id, threshold=999999)
        print("  OK - compression path completed without external call")

        print("--- Test 10: Bubble Quotes ---")
        assert len(BUBBLE_QUOTES) >= 10
        print(f"  OK - {len(BUBBLE_QUOTES)} quotes available")

        print()
        print("=" * 50)
        print("  ALL 10 SMOKE TESTS PASSED")
        print("=" * 50)
    finally:
        await close_database()
        _tmpdir.cleanup()


if __name__ == "__main__":
    asyncio.run(run_all())
