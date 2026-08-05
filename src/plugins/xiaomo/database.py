"""小源 QQ 机器人 - 数据库层 (SQLite + SQLAlchemy async)"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    update,
    select,
    desc,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import get_config

# ─── Base & Engine ────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker | None = None


async def init_database():
    global _engine, _session_factory
    if _engine is not None and _session_factory is not None:
        return

    config = get_config()
    db_path = config.get("database_path", "data/xiaomo.db")
    # Ensure absolute path
    if not Path(db_path).is_absolute():
        project_root = Path(__file__).parent.parent.parent.parent
        db_path = str(project_root / db_path)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(_engine.sync_engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _session_factory()


async def close_database():
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None


# ─── Models ───────────────────────────────────────────────────────────────────


class User(Base):
    """用户表 — 以 QQ 号为唯一主键"""
    __tablename__ = "users"

    qq_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    first_seen: Mapped[float] = mapped_column(Float, default=time.time)
    last_seen: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    # 扩展画像 JSON: {preferred_name, topics_of_interest, personality_notes, ...}
    profile_data: Mapped[Optional[str]] = mapped_column(Text, default="{}")

    # Relationships
    nicknames = relationship("UserNickname", back_populates="user", lazy="noload")
    messages = relationship("Message", back_populates="user", lazy="noload")

    def get_profile(self) -> dict:
        return json.loads(self.profile_data or "{}")

    def set_profile(self, data: dict):
        self.profile_data = json.dumps(data, ensure_ascii=False)


class Nickname(Base):
    """昵称/称号表"""
    __tablename__ = "nicknames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    users = relationship("UserNickname", back_populates="nickname")


class UserNickname(Base):
    """用户-昵称 多对多关联"""
    __tablename__ = "user_nicknames"

    user_qq: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.qq_id"), primary_key=True
    )
    nickname_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nicknames.id"), primary_key=True
    )
    source: Mapped[Optional[str]] = mapped_column(String(256), default=None)  # 谁起的

    user = relationship("User", back_populates="nicknames")
    nickname = relationship("Nickname", back_populates="users")


class Relationship(Base):
    """用户关系图 — 多对多边表"""
    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_qq: Mapped[str] = mapped_column(String(32), ForeignKey("users.qq_id"), nullable=False)
    to_qq: Mapped[str] = mapped_column(String(32), ForeignKey("users.qq_id"), nullable=False)
    relation_type: Mapped[Optional[str]] = mapped_column(
        String(64), default=None
    )  # friend, mentioned, close_friend, etc.
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (
        Index("idx_rel_from", "from_qq"),
        Index("idx_rel_to", "to_qq"),
    )


class Message(Base):
    """聊天消息表 — 带权重衰减"""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_qq: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.qq_id"), nullable=True, default=None
    )
    group_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None)
    scene: Mapped[str] = mapped_column(String(16), nullable=False)  # 'private' | 'group'
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'user' | 'assistant' | 'system'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    image_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    user = relationship("User", back_populates="messages")

    __table_args__ = (
        Index("idx_msg_user", "user_qq"),
        Index("idx_msg_group", "group_id"),
        Index("idx_msg_scene_user", "scene", "user_qq"),
        Index("idx_msg_scene_group", "scene", "group_id"),
        Index("idx_msg_created", "created_at"),
    )

    def current_weight(self, half_life_minutes: float = 60) -> float:
        """基于当前时间计算衰减后的权重"""
        age_minutes = (time.time() - self.created_at) / 60
        return self.weight * math.exp(-age_minutes * math.log(2) / half_life_minutes)


class ContextSummary(Base):
    """旧记忆压缩摘要"""
    __tablename__ = "context_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_qq: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.qq_id"), nullable=True, default=None
    )
    group_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None)
    scene: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    start_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    __table_args__ = (
        Index("idx_summary_scene_user", "scene", "user_qq"),
        Index("idx_summary_scene_group", "scene", "group_id"),
    )


class InboundEvent(Base):
    """Map a OneBot group event to its stored message.

    Keeping transport IDs separate avoids changing the existing messages table and
    gives us both replay deduplication and reliable reply-target lookup.
    """

    __tablename__ = "inbound_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_qq: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "source_message_id",
            name="uq_inbound_group_source",
        ),
        Index("idx_inbound_message", "message_id"),
    )


class RuntimeState(Base):
    """Small durable snapshots for cooldowns and participation feedback."""

    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


# ─── Repository Helpers ──────────────────────────────────────────────────────


async def get_or_create_user(session: AsyncSession, qq_id: str) -> User:
    result = await session.execute(select(User).where(User.qq_id == qq_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(qq_id=qq_id)
        session.add(user)
        await session.flush()
    return user


async def save_message(
    session: AsyncSession,
    user_qq: str | None,
    group_id: str | None,
    scene: str,
    role: str,
    content: str,
    image_url: str | None = None,
    image_description: str | None = None,
) -> Message:
    # Ensure user exists before creating message (avoids FK constraint issues)
    if user_qq:
        user = await get_or_create_user(session, user_qq)
        user.total_messages = (user.total_messages or 0) + 1
        user.last_seen = time.time()

    msg = Message(
        user_qq=user_qq,
        group_id=group_id,
        scene=scene,
        role=role,
        content=content,
        image_url=image_url,
        image_description=image_description,
    )
    session.add(msg)

    await session.flush()
    return msg


async def save_inbound_group_message(
    session: AsyncSession,
    *,
    group_id: str,
    source_message_id: str,
    user_qq: str,
    content: str,
    nickname: str | None = None,
    image_url: str | None = None,
    image_description: str | None = None,
) -> tuple[Message | None, bool, User | None]:
    """Store one incoming group event exactly once.

    The reservation insert and message write share a transaction. A replay sees
    the unique transport key and reuses the original message instead of replying
    twice or inflating user statistics.
    """

    insert_result = await session.execute(
        sqlite_insert(InboundEvent)
        .values(
            group_id=group_id,
            source_message_id=source_message_id,
            user_qq=user_qq,
        )
        .on_conflict_do_nothing(
            index_elements=["group_id", "source_message_id"]
        )
    )
    if insert_result.rowcount == 0:
        existing_result = await session.execute(
            select(Message)
            .join(InboundEvent, InboundEvent.message_id == Message.id)
            .where(
                InboundEvent.group_id == group_id,
                InboundEvent.source_message_id == source_message_id,
            )
        )
        return existing_result.scalar_one_or_none(), False, None

    user = await get_or_create_user(session, user_qq)
    if nickname and user.nickname != nickname:
        user.nickname = nickname
    user.total_messages = (user.total_messages or 0) + 1
    user.last_seen = time.time()

    message = Message(
        user_qq=user_qq,
        group_id=group_id,
        scene="group",
        role="user",
        content=content,
        image_url=image_url,
        image_description=image_description,
    )
    session.add(message)
    await session.flush()
    await session.execute(
        update(InboundEvent)
        .where(
            InboundEvent.group_id == group_id,
            InboundEvent.source_message_id == source_message_id,
        )
        .values(message_id=message.id)
    )
    return message, True, user


async def get_message_by_source_id(
    session: AsyncSession,
    *,
    group_id: str,
    source_message_id: str,
) -> Message | None:
    result = await session.execute(
        select(Message)
        .join(InboundEvent, InboundEvent.message_id == Message.id)
        .where(
            InboundEvent.group_id == group_id,
            InboundEvent.source_message_id == source_message_id,
        )
    )
    return result.scalar_one_or_none()


async def link_source_message_id(
    session: AsyncSession,
    *,
    group_id: str,
    source_message_id: str,
    message_id: int,
    user_qq: str | None = None,
) -> None:
    await session.execute(
        sqlite_insert(InboundEvent)
        .values(
            group_id=group_id,
            source_message_id=source_message_id,
            message_id=message_id,
            user_qq=user_qq,
        )
        .on_conflict_do_update(
            index_elements=["group_id", "source_message_id"],
            set_={"message_id": message_id, "user_qq": user_qq},
        )
    )


async def update_message_content(
    session: AsyncSession,
    message_id: int,
    *,
    content: str,
    image_url: str | None = None,
    image_description: str | None = None,
) -> None:
    await session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(
            content=content,
            image_url=image_url,
            image_description=image_description,
        )
    )


async def get_context_messages(
    session: AsyncSession,
    scene: str,
    user_qq: str | None = None,
    group_id: str | None = None,
    half_life_minutes: float = 60,
    limit: int = 200,
) -> list[Message]:
    """
    获取按权重衰减排序的上下文消息。
    返回 limit 条消息，越新越重的优先。
    """
    stmt = select(Message).where(Message.scene == scene)

    if scene == "private" and user_qq:
        stmt = stmt.where(Message.user_qq == user_qq).where(Message.group_id.is_(None))
    elif scene == "group" and group_id:
        stmt = stmt.where(Message.group_id == group_id)

    stmt = stmt.order_by(desc(Message.created_at), desc(Message.id)).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_profile_summary(session: AsyncSession, qq_id: str) -> dict:
    """获取用户画像摘要，包含昵称和关系"""
    user = await session.execute(select(User).where(User.qq_id == qq_id))
    user = user.scalar_one_or_none()
    if user is None:
        return {"qq_id": qq_id, "exists": False}

    # 获取所有昵称
    nick_stmt = (
        select(Nickname.name)
        .join(UserNickname)
        .where(UserNickname.user_qq == qq_id)
    )
    nick_result = await session.execute(nick_stmt)
    nicknames = [row[0] for row in nick_result.all()]

    # 获取关系列表
    rel_stmt = select(Relationship).where(Relationship.from_qq == qq_id)
    rel_result = await session.execute(rel_stmt)
    relationships = [
        {"to": r.to_qq, "type": r.relation_type, "weight": r.weight}
        for r in rel_result.scalars().all()
    ]

    return {
        "qq_id": qq_id,
        "exists": True,
        "nickname": user.nickname,
        "nicknames": nicknames,
        "first_seen": user.first_seen,
        "total_messages": user.total_messages,
        "profile": user.get_profile(),
        "relationships": relationships,
    }


async def get_user_display_names(
    session: AsyncSession,
    qq_ids: Iterable[str | None],
) -> dict[str, str]:
    """Batch-load stable display names for group context rendering."""
    ids = sorted({str(q) for q in qq_ids if q})
    if not ids:
        return {}

    result = await session.execute(select(User).where(User.qq_id.in_(ids)))
    users = list(result.scalars().all())

    names: dict[str, str] = {}
    for user in users:
        profile = user.get_profile()
        name = user.nickname or profile.get("preferred_name") or ""
        if name:
            names[user.qq_id] = str(name)

    return names
