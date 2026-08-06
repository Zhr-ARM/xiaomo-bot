"""小源 QQ 机器人 - 记忆系统

上下文权重衰减 + 用户画像 + 记忆压缩
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable

from sqlalchemy import select, desc, delete

import logging

from .config import get_config
from .database import (
    ContextSummary,
    Message,
    get_context_messages,
    get_session,
    get_user_display_names,
    get_user_profile_summary,
    save_inbound_group_message,
    save_message,
)

logger = logging.getLogger("xiaomo.memory")
_compression_tasks: dict[str, asyncio.Task] = {}
_vector_index_tasks: set[asyncio.Task] = set()
_NON_SEMANTIC_PLACEHOLDERS = {"[非文本群消息]", "[有成员@了小源]"}


def _schedule_vector_index(**kwargs) -> None:
    if len(_vector_index_tasks) >= 256:
        logger.warning("Vector index backlog is full; skipping one message")
        return

    async def _run() -> None:
        from .vector_store import add_message

        await add_message(**kwargs)

    task = asyncio.create_task(_run(), name="xiaomo-vector-index-message")
    _vector_index_tasks.add(task)

    def _finished(done: asyncio.Task) -> None:
        _vector_index_tasks.discard(done)
        exception = None if done.cancelled() else done.exception()
        if exception is not None:
            logger.error(
                "Background vector indexing failed",
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    task.add_done_callback(_finished)


def schedule_vector_refresh(
    *,
    message_id: int,
    content: str,
    user_qq: str | None,
    group_id: str | None,
    scene: str,
    created_at: float,
) -> None:
    """Upsert a message after asynchronous enrichment such as vision recognition."""
    if not content.strip() or content.strip() in _NON_SEMANTIC_PLACEHOLDERS:
        return
    _schedule_vector_index(
        message_id=message_id,
        content=content,
        user_qq=user_qq,
        group_id=group_id,
        scene=scene,
        created_at=created_at,
    )


# ─── Weight Decay ─────────────────────────────────────────────────────────────


def calc_weight(
    created_at: float,
    base_weight: float = 1.0,
    half_life_minutes: float = 60,
    now: float | None = None,
) -> float:
    """指数衰减权重：weight * 2^(-age/half_life)"""
    if now is None:
        now = time.time()
    age = (now - created_at) / 60
    return base_weight * math.exp(-age * math.log(2) / half_life_minutes)


# ─── Conversation Key ─────────────────────────────────────────────────────────


def private_key(qq_id: str) -> str:
    return f"private:{qq_id}"


def group_key(group_id: str) -> str:
    return f"group:{group_id}"


# ─── Context Builder ──────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def _smart_truncate(
    messages: list[Message], max_tokens: int, half_life: float
) -> list[Message]:
    """
    智能上下文截断：始终保留最近 N 条，旧消息按权重择优保留。
    """
    if not messages:
        return []

    config = get_config()
    keep_recent = config.get("memory", {}).get("keep_recent_messages", 50)

    sorted_by_time = sorted(messages, key=lambda m: m.created_at, reverse=True)
    recent = sorted_by_time[:keep_recent]
    old = sorted_by_time[keep_recent:]

    # 历史消息按权重排序
    old_with_weight = [(m, m.current_weight(half_life)) for m in old]
    old_with_weight.sort(key=lambda x: x[1], reverse=True)

    selected: list[Message] = []
    used_tokens = 0

    def try_add(msg: Message) -> None:
        nonlocal used_tokens
        cost = max(1, estimate_tokens(msg.content))
        if used_tokens + cost <= max_tokens:
            selected.append(msg)
            used_tokens += cost

    # 先保留最近消息，再用剩余预算补充高权重旧消息。
    for msg in recent:
        try_add(msg)
    for msg, _ in old_with_weight:
        try_add(msg)

    selected.sort(key=lambda m: m.created_at)
    return selected


def _latest_user_content(messages: list[Message], user_qq: str | None) -> str:
    """获取当前用户最近一条非空用户消息，用作语义搜索 query。"""
    for msg in sorted(messages, key=lambda m: m.created_at, reverse=True):
        if msg.role != "user":
            continue
        if user_qq and msg.user_qq != user_qq:
            continue
        content = (msg.content or "").strip()
        if content:
            return content
    return ""


def _speaker_label(user_qq: str | None, user_names: dict[str, str]) -> str:
    """Render identity with QQ as the stable key and nickname as display only."""

    qq = str(user_qq or "").strip()
    if not qq:
        return "未知成员 (QQ:unknown)"
    name = user_names.get(qq) or f"QQ{qq}"
    return f"{name} (QQ:{qq})"


async def build_context(
    scene: str,
    user_qq: str | None = None,
    group_id: str | None = None,
    half_life_minutes: float = 60,
    max_tokens: int = 8000,
    current_query: str | None = None,
    exclude_message_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> tuple[str, dict, list[dict]]:
    """
    构建 LLM 上下文：
    - 从数据库加载相关消息
    - 按权重衰减排序
    - 压缩旧消息为摘要
    - 返回格式化的上下文
    """
    excluded_ids: set[int] = set()
    for mid in exclude_message_ids or []:
        if mid is None:
            continue
        try:
            excluded_ids.add(int(mid))
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid exclude_message_id: %r", mid)

    async with await get_session() as session:
        messages = await get_context_messages(
            session,
            scene=scene,
            user_qq=user_qq,
            group_id=group_id,
            half_life_minutes=half_life_minutes,
            limit=500,
        )
        if excluded_ids:
            messages = [m for m in messages if m.id not in excluded_ids]

        # 获取压缩摘要
        summary_stmt = select(ContextSummary).where(ContextSummary.scene == scene)
        if scene == "private" and user_qq:
            summary_stmt = summary_stmt.where(ContextSummary.user_qq == user_qq)
        elif scene == "group" and group_id:
            summary_stmt = summary_stmt.where(ContextSummary.group_id == group_id)
        summary_stmt = summary_stmt.order_by(
            desc(ContextSummary.created_at),
            desc(ContextSummary.id),
        ).limit(5)
        summary_result = await session.execute(summary_stmt)
        summaries = list(summary_result.scalars().all())

        # 用户画像
        profile = {}
        if user_qq:
            profile = await get_user_profile_summary(session, user_qq)

        # 分用户记忆：当前说话人的历史发言
        user_history: list[Message] = []
        if user_qq and scene == "group" and group_id:
            user_history_stmt = (
                select(Message)
                .where(
                    Message.scene == "group",
                    Message.group_id == group_id,
                    Message.user_qq == user_qq,
                    Message.role == "user",
                )
                .order_by(desc(Message.created_at), desc(Message.id))
                .limit(30)
            )
            user_history_result = await session.execute(user_history_stmt)
            user_history = list(user_history_result.scalars().all())
            if excluded_ids:
                user_history = [m for m in user_history if m.id not in excluded_ids]

        name_ids = {m.user_qq for m in messages if m.user_qq}
        name_ids.update(m.user_qq for m in user_history if m.user_qq)
        if user_qq:
            name_ids.add(user_qq)
        user_names = await get_user_display_names(session, name_ids)

    # 截断
    messages = _smart_truncate(list(messages), max_tokens, half_life_minutes)

    # 构建上下文文本和结构化消息历史
    context_parts: list[str] = []
    structured_history: list[dict] = []

    if summaries:
        context_parts.append(
            "[以下是群级历史摘要。它不是当前成员的个人画像；只有明确标注同一 QQ 的事实才能归给该成员]\n"
        )
        for s in summaries:
            summary = str(s.summary or "").strip()
            if scene == "group" and "QQ:" not in summary:
                context_parts.append(
                    "[旧摘要未保留可靠 QQ，仅可用作话题背景，禁止把其中人物信息归给当前成员]\n"
                    f"{summary}"
                )
            else:
                context_parts.append(summary)
        context_parts.append("\n[/历史摘要]\n")

    # 分用户记忆：当前说话人的历史发言
    if user_history:
        context_parts.append(
            f"[以下仅是当前成员 QQ:{user_qq} 近期在本群的发言历史]\n"
        )
        for m in reversed(user_history):
            context_parts.append(
                f"- [{_speaker_label(m.user_qq, user_names)}]: {m.content[:300]}"
            )
        context_parts.append("\n[/成员发言历史]\n")

    # 向量语义搜索：查找与当前话题相似的历史消息
    if scene == "group" and group_id:
        try:
            # 用最近的用户消息作为搜索 query
            latest_user_content = (current_query or "").strip() or _latest_user_content(messages, user_qq)
            if latest_user_content:
                from .vector_store import search_similar
                # 排除最近 10 分钟的消息（已在上下文中）
                recent_cutoff = time.time() - 600
                vector_hits = await search_similar(
                    query=latest_user_content,
                    scene=scene,
                    group_id=group_id,
                    user_qq=user_qq,
                    n_results=10,
                    min_time=0,
                    max_time=recent_cutoff,
                )
                if vector_hits:
                    missing_names = {
                        h.get("user_qq")
                        for h in vector_hits
                        if h.get("user_qq") and h.get("user_qq") not in user_names
                    }
                    if missing_names:
                        async with await get_session() as session:
                            user_names.update(await get_user_display_names(session, missing_names))
                    context_parts.append(
                        f"[以下仅是当前成员 QQ:{user_qq} 的语义相关历史发言]\n"
                    )
                    for h in vector_hits:
                        user_label = _speaker_label(h.get("user_qq"), user_names)
                        context_parts.append(f"- [{user_label}]: {h['content'][:300]}")
                    context_parts.append("\n[/语义相关历史]\n")
        except Exception:
            pass  # 向量搜索失败不影响主流程

    for msg in messages:
        if msg.role == "assistant":
            name = "小源"
            structured_history.append({"role": "assistant", "content": msg.content})
        elif msg.role == "user":
            name = _speaker_label(msg.user_qq, user_names)
            structured_history.append(
                {"role": "user", "content": f"[{name}]: {msg.content}"}
            )
        else:
            name = "系统"

    # 合并连续同角色消息，确保 user/assistant 交替
    merged_history: list[dict] = []
    for msg in structured_history:
        if merged_history and merged_history[-1]["role"] == msg["role"]:
            merged_history[-1]["content"] += "\n" + msg["content"]
        else:
            merged_history.append(msg)

    context_text = "\n".join(context_parts)
    return context_text, {
        "profile": profile,
        "message_count": len(messages),
        "identity_qq": str(user_qq or ""),
    }, merged_history


async def store_memory(
    user_qq: str | None,
    group_id: str | None,
    scene: str,
    role: str,
    content: str,
    image_url: str | None = None,
    image_description: str | None = None,
) -> int:
    """存储消息到记忆系统"""
    async with await get_session() as session:
        msg = await save_message(
            session,
            user_qq=user_qq,
            group_id=group_id,
            scene=scene,
            role=role,
            content=content,
            image_url=image_url,
            image_description=image_description,
        )
        await session.commit()

    # 同步写向量库（用户消息才需要语义搜索）
    if (
        role == "user"
        and content.strip()
        and content.strip() not in _NON_SEMANTIC_PLACEHOLDERS
    ):
        _schedule_vector_index(
            message_id=msg.id,
            content=content,
            user_qq=user_qq,
            group_id=group_id,
            scene=scene,
            created_at=msg.created_at,
        )

    return msg.id


async def store_inbound_memory(
    *,
    group_id: str,
    source_message_id: str,
    user_qq: str,
    nickname: str | None,
    content: str,
    profile_learner: Callable[[dict, str], dict] | None = None,
) -> tuple[int | None, bool]:
    """Persist an incoming OneBot message once and update its sender atomically."""

    message = None
    created = False
    async with await get_session() as session:
        message, created, user = await save_inbound_group_message(
            session,
            group_id=group_id,
            source_message_id=source_message_id,
            user_qq=user_qq,
            nickname=nickname,
            content=content,
        )
        if created and user is not None and profile_learner is not None:
            current_profile = user.get_profile()
            learned_profile = profile_learner(current_profile, content)
            if learned_profile != current_profile:
                user.set_profile(learned_profile)
        await session.commit()

    if (
        created
        and message is not None
        and content.strip()
        and content.strip() not in _NON_SEMANTIC_PLACEHOLDERS
    ):
        _schedule_vector_index(
            message_id=message.id,
            content=content,
            user_qq=user_qq,
            group_id=group_id,
            scene="group",
            created_at=message.created_at,
        )

    return (message.id if message is not None else None), created


async def compress_old_memories(
    scene: str,
    user_qq: str | None = None,
    group_id: str | None = None,
    threshold: int = 15000,
):
    """
    压缩旧记忆：当消息总 token 超过阈值时，将最旧的消息压缩为摘要。
    """
    async with await get_session() as session:
        stmt = select(Message).where(Message.scene == scene)
        if scene == "private" and user_qq:
            stmt = stmt.where(Message.user_qq == user_qq)
        elif scene == "group" and group_id:
            stmt = stmt.where(Message.group_id == group_id)

        stmt = stmt.order_by(desc(Message.created_at), desc(Message.id))
        result = await session.execute(stmt)
        messages = list(result.scalars().all())

        total_tokens = sum(estimate_tokens(m.content) for m in messages)
        if total_tokens < threshold:
            return

        config = get_config()
        keep_recent = config.get("memory", {}).get("keep_recent_messages", 50)

        to_compress = messages[keep_recent:]
        if len(to_compress) < 10:
            return

        # Only delete messages represented by this summary. The query is newest
        # first, so the tail is the oldest bounded batch.
        compress_batch = to_compress[-100:]

        # 构建压缩文本
        identity_names = await get_user_display_names(
            session,
            {m.user_qq for m in compress_batch if m.user_qq},
        )
        bot_qq = str(get_config().get("bot", {}).get("qq_id") or "bot")
        compress_text_parts = []
        for m in reversed(compress_batch):
            role_name = (
                _speaker_label(m.user_qq, identity_names)
                if m.role == "user"
                else f"小源 (QQ:{bot_qq})"
            )
            compress_text_parts.append(f"[{role_name}]: {m.content[:300]}")
        compress_text = "\n".join(compress_text_parts)

        # 调 LLM 生成高质量摘要
        try:
            from .llm import get_llm

            llm = get_llm()
            summary_text = await llm.generate_summary(compress_text)
            if not summary_text:
                raise ValueError("LLM returned empty summary")
        except Exception:
            logger.exception("LLM summarization failed, falling back to raw text")
            summary_text = "对话摘要：\n" + compress_text[:3000]

        compressed = ContextSummary(
            user_qq=user_qq if scene == "private" else None,
            group_id=group_id,
            scene=scene,
            summary=summary_text,
            start_message_id=min(m.id for m in compress_batch),
            end_message_id=max(m.id for m in compress_batch),
        )
        session.add(compressed)

        compress_ids = [m.id for m in compress_batch]
        await session.execute(delete(Message).where(Message.id.in_(compress_ids)))
        await session.commit()

        try:
            from .vector_store import delete_messages
            await delete_messages(compress_ids)
        except Exception:
            logger.exception("Failed to clean compressed vector memories")


def schedule_memory_compression(
    scene: str,
    user_qq: str | None,
    group_id: str | None,
    threshold: int,
) -> None:
    """Run at most one background compression job per conversation."""

    key = f"{scene}:{group_id or user_qq or 'global'}"
    current = _compression_tasks.get(key)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(
        compress_old_memories(scene, user_qq, group_id, threshold),
        name=f"xiaomo-memory-compress-{key}",
    )
    _compression_tasks[key] = task

    def _finished(done: asyncio.Task) -> None:
        _compression_tasks.pop(key, None)
        exception = None if done.cancelled() else done.exception()
        if exception is not None:
            logger.error(
                "Background memory compression failed",
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    task.add_done_callback(_finished)


async def close_memory_tasks() -> None:
    tasks = list(_compression_tasks.values()) + list(_vector_index_tasks)
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _compression_tasks.clear()
    _vector_index_tasks.clear()
