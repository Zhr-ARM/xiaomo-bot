"""小源 QQ 机器人 - 记忆系统

上下文权重衰减 + 用户画像 + 记忆压缩
"""
from __future__ import annotations

import math
import time

from sqlalchemy import select, desc, delete

import logging

from .config import get_config
from .database import (
    ContextSummary,
    Message,
    get_context_messages,
    get_session,
    get_user_profile_summary,
    save_message,
)

logger = logging.getLogger("xiaomo.memory")


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
    messages: list[Message], _max_tokens: int, half_life: float
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

    selected = recent + [m for m, _ in old_with_weight]
    selected.sort(key=lambda m: m.created_at)
    return selected


async def build_context(
    scene: str,
    user_qq: str | None = None,
    group_id: str | None = None,
    half_life_minutes: float = 60,
    max_tokens: int = 8000,
) -> tuple[str, dict]:
    """
    构建 LLM 上下文：
    - 从数据库加载相关消息
    - 按权重衰减排序
    - 压缩旧消息为摘要
    - 返回格式化的上下文
    """
    async with await get_session() as session:
        messages = await get_context_messages(
            session,
            scene=scene,
            user_qq=user_qq,
            group_id=group_id,
            half_life_minutes=half_life_minutes,
            limit=500,
        )

        # 获取压缩摘要
        summary_stmt = select(ContextSummary).where(ContextSummary.scene == scene)
        if scene == "private" and user_qq:
            summary_stmt = summary_stmt.where(ContextSummary.user_qq == user_qq)
        elif scene == "group" and group_id:
            summary_stmt = summary_stmt.where(ContextSummary.group_id == group_id)
        summary_stmt = summary_stmt.order_by(desc(ContextSummary.created_at)).limit(5)
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
                .order_by(desc(Message.created_at))
                .limit(30)
            )
            user_history_result = await session.execute(user_history_stmt)
            user_history = list(user_history_result.scalars().all())

    # 截断
    messages = _smart_truncate(list(messages), max_tokens, half_life_minutes)

    # 构建上下文文本
    parts = []

    if summaries:
        parts.append("[以下是历史对话摘要]\n")
        for s in summaries:
            parts.append(s.summary)
        parts.append("\n[/历史摘要]\n")

    # Build user name lookup for personalized context
    user_names: dict[str, str] = {}
    if profile and profile.get("exists"):
        qq = profile["qq_id"]
        name = profile.get("nickname", "") or profile.get("profile", {}).get("preferred_name", "")
        if name:
            user_names[qq] = name

    # 分用户记忆：当前说话人的历史发言
    if user_history:
        parts.append("[以下是该成员近期在本群的发言历史]\n")
        for m in reversed(user_history):
            parts.append(f"- {m.content[:300]}")
        parts.append("\n[/成员发言历史]\n")

    # 向量语义搜索：查找与当前话题相似的历史消息
    if scene == "group" and group_id:
        try:
            # 用最近的用户消息作为搜索 query
            latest_user_content = ""
            for m in messages:
                if m.role == "user" and m.user_qq == user_qq:
                    latest_user_content = m.content
                    break
            if latest_user_content:
                from .vector_store import search_similar
                # 排除最近 10 分钟的消息（已在上下文中）
                min_age = __import__("time").time() - 600
                vector_hits = await search_similar(
                    query=latest_user_content,
                    scene=scene,
                    group_id=group_id,
                    n_results=10,
                    min_time=min_age,
                )
                if vector_hits:
                    parts.append("[以下是语义相关的历史讨论（可能有助于回答当前问题）]\n")
                    for h in vector_hits:
                        user_label = f"QQ{h['user_qq']}" if h["user_qq"] else "未知"
                        parts.append(f"- [{user_label}]: {h['content'][:300]}")
                    parts.append("\n[/语义相关历史]\n")
        except Exception:
            pass  # 向量搜索失败不影响主流程

    for msg in messages:
        if msg.role == "assistant":
            name = "小源"
        elif msg.role == "user":
            name = user_names.get(msg.user_qq or "", f"用户{msg.user_qq}" if msg.user_qq else "用户")
        else:
            name = "系统"
        parts.append(f"[{name}]: {msg.content}")

    return "\n".join(parts), {"profile": profile, "message_count": len(messages)}


async def store_memory(
    user_qq: str | None,
    group_id: str | None,
    scene: str,
    role: str,
    content: str,
    image_url: str | None = None,
    image_description: str | None = None,
):
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
    if role == "user" and content.strip():
        try:
            from .vector_store import add_message
            await add_message(
                message_id=msg.id,
                content=content,
                user_qq=user_qq,
                group_id=group_id,
                scene=scene,
                created_at=msg.created_at,
            )
        except Exception:
            logger.exception("Failed to add to vector store")


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

        stmt = stmt.order_by(desc(Message.created_at))
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

        # 构建压缩文本
        compress_text_parts = []
        for m in reversed(to_compress[-100:]):  # 取最近 100 条，按时间正序
            role_name = "用户" if m.role == "user" else "小源"
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
            user_qq=user_qq,
            group_id=group_id,
            scene=scene,
            summary=summary_text,
            start_message_id=to_compress[-1].id,
            end_message_id=to_compress[0].id,
        )
        session.add(compressed)

        compress_ids = [m.id for m in to_compress]
        await session.execute(delete(Message).where(Message.id.in_(compress_ids)))
        await session.commit()
