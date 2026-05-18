"""集成测试脚本 - 测试小源机器人各模块"""
import asyncio
import time
import nonebot

nonebot.init()

from sqlalchemy import delete
from src.plugins.xiaomo.database import (
    init_database, get_session, Message, User
)
from src.plugins.xiaomo.memory import (
    store_memory, build_context, calc_weight, compress_old_memories
)
from src.plugins.xiaomo.window import SilentWindow
from src.plugins.xiaomo.auto_action import (
    check_repeat, detect_interesting_topic, BUBBLE_QUOTES
)
from src.plugins.xiaomo.filter_utils import check_content_safe, extract_code_blocks


async def test_all():
    # Init DB
    await init_database()

    # Clean
    async with await get_session() as session:
        await session.execute(delete(Message))
        await session.execute(delete(User))
        await session.commit()

    # Test 1: Store messages
    print("--- Test 1: Conversation Storage ---")
    await store_memory("111", None, "private", "user", "hello")
    await store_memory(None, None, "private", "assistant", "hi there")
    await store_memory("111", None, "private", "user", "how are you")
    await store_memory(None, None, "private", "assistant", "great!")
    print("  OK - 4 messages stored")

    # Test 2: Context building
    print("--- Test 2: Context Building ---")
    ctx, meta = await build_context("private", user_qq="111")
    assert meta["message_count"] >= 2
    assert meta["profile"]["exists"]
    print(f"  OK - {meta['message_count']} messages in context")

    # Test 3: Weight decay
    print("--- Test 3: Weight Decay ---")
    now = time.time()
    w0 = calc_weight(now, half_life_minutes=60)
    w1 = calc_weight(now - 3600, half_life_minutes=60)
    w2 = calc_weight(now - 7200, half_life_minutes=60)
    assert 0.49 < w1 < 0.51, f"1h weight should be ~0.5, got {w1}"
    assert 0.24 < w2 < 0.26, f"2h weight should be ~0.25, got {w2}"
    print(f"  OK - weights: now={w0:.3f} 1h={w1:.3f} 2h={w2:.3f}")

    # Test 4: Silent window
    print("--- Test 4: Silent Window ---")
    w = SilentWindow()
    fired = []

    async def cb(key, msgs):
        fired.append((key, msgs))

    w.set_callback(cb)
    w.enqueue("test_key", {"text": "hello"}, is_group=False)
    await asyncio.sleep(0.5)
    assert len(fired) == 0, "should not fire yet"
    await asyncio.sleep(3.0)
    assert len(fired) == 1, "should fire after window"
    print("  OK - fired after delay")

    # Test 5: Repeat detection
    print("--- Test 5: Repeat Detection ---")
    # Reset state
    from src.plugins.xiaomo import state
    state.repeat_counter.clear()
    state.repeat_lock.clear()
    state.repeat_last_time.clear()

    await check_repeat("g1", "haha")
    await check_repeat("g1", "haha")
    r = await check_repeat("g1", "haha")
    assert r == "haha", f"should detect repeat, got {r}"
    print("  OK - repeat detected:", r)

    # Test 6: Topic detection
    print("--- Test 6: Topic Detection ---")
    t = detect_interesting_topic("bug")
    assert t == "bug"
    t2 = detect_interesting_topic("normal chat")
    assert t2 is None
    print("  OK - topic detection works")

    # Test 7: Content filter
    print("--- Test 7: Content Filter ---")
    ok, _ = check_content_safe("hello world")
    assert ok
    print("  OK - safe content passes")

    # Test 8: Code extraction
    print("--- Test 8: Code Extraction ---")
    blocks = extract_code_blocks("```python\nprint(1)\n```")
    assert len(blocks) == 1
    assert blocks[0]["language"] == "python"
    print("  OK - code block extracted")

    # Test 9: Memory compression
    print("--- Test 9: Memory Compression ---")
    for i in range(60):
        await store_memory("111", None, "private", "user", "test " * 20)
    await compress_old_memories("private", user_qq="111", threshold=500)
    print("  OK - compression completed")

    # Test 10: Bubble quotes
    print("--- Test 10: Bubble Quotes ---")
    assert len(BUBBLE_QUOTES) == 12
    print(f"  OK - {len(BUBBLE_QUOTES)} quotes available")

    print()
    print("=" * 50)
    print("  ALL 10 INTEGRATION TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_all())
