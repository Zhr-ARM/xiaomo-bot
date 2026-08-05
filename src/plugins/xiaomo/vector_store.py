"""小源 QQ 机器人 - 向量记忆存储 (ChromaDB + sentence-transformers)"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from pathlib import Path

from .config import get_config

logger = logging.getLogger("xiaomo.vector_store")

_collection = None
_embedding_model = None
_init_task: asyncio.Task | None = None
_store_lock = threading.RLock()
_status = "not_started"
_last_error: str | None = None
_document_count = 0
_pending_writes: deque[tuple[int, str, str | None, str | None, str, float]] = deque(
    maxlen=1000
)


def _get_data_dir() -> Path:
    config = get_config()
    db_path = config.get("database_path", "data/xiaomo.db")
    return Path(db_path).parent


def _load_embedding_model(model_name: str, model_class):
    """Prefer an already cached model and only contact Hugging Face if needed."""
    try:
        logger.info("Loading embedding model from local cache: %s", model_name)
        return model_class(model_name, local_files_only=True)
    except Exception as local_error:
        logger.warning(
            "Embedding model is not usable from the local cache; downloading %s: %s",
            model_name,
            local_error,
        )
        return model_class(model_name)


def _load_vector_store_sync():
    """Load the local embedding stack without blocking NoneBot's event loop."""
    # 国内 HuggingFace 镜像，加速模型下载
    if "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    import chromadb
    from sentence_transformers import SentenceTransformer

    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # 向量存储
    client = chromadb.PersistentClient(path=str(data_dir / "vector_db"))
    collection = client.get_or_create_collection(
        name="chat_memory",
        metadata={"hnsw:space": "cosine"},
    )

    # 本地 embedding 模型
    model_name = get_config().get("vector", {}).get("model", "BAAI/bge-small-zh-v1.5")
    embedding_model = _load_embedding_model(model_name, SentenceTransformer)
    return collection, embedding_model


async def init_vector_store() -> bool:
    """Initialize semantic memory in a worker thread and degrade gracefully."""
    global _collection, _document_count, _embedding_model, _last_error, _status

    if _collection is not None and _embedding_model is not None:
        return True

    _status = "initializing"
    _last_error = None
    try:
        collection, embedding_model = await asyncio.to_thread(_load_vector_store_sync)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _status = "degraded"
        _last_error = str(error)[:300]
        logger.exception("Vector store initialization failed; semantic recall is disabled")
        return False

    _collection = collection
    _embedding_model = embedding_model
    pending = list(_pending_writes)
    _pending_writes.clear()
    if pending:
        await asyncio.to_thread(_add_messages_sync, pending)
    _document_count = await asyncio.to_thread(_collection_count_sync)
    _status = "ready"
    logger.info("Vector store initialized (collection: %d docs)", _document_count)
    return True


async def _init_with_retry() -> bool:
    for attempt, delay in enumerate((0.0, 5.0, 30.0), start=1):
        if delay:
            await asyncio.sleep(delay)
        if await init_vector_store():
            return True
        logger.warning("Vector store init attempt %d failed", attempt)
    return False


def start_vector_store_init() -> asyncio.Task:
    """Start semantic-memory initialization without delaying bot readiness."""
    global _init_task
    if _init_task is None or _init_task.done():
        _init_task = asyncio.create_task(
            _init_with_retry(),
            name="xiaomo-vector-store-init",
        )
        logger.info("Vector store initialization scheduled in background")
    return _init_task


def get_vector_status() -> dict[str, object]:
    """Return a non-blocking semantic-memory status snapshot for health checks."""
    result: dict[str, object] = {
        "status": _status,
        "pending_writes": len(_pending_writes),
    }
    if _status == "ready":
        result["documents"] = _document_count
    if _last_error:
        result["last_error"] = _last_error
    return result


def _embed(texts: list[str]) -> list[list[float]]:
    if _embedding_model is None:
        raise RuntimeError("Vector store not initialized")
    embeddings = _embedding_model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def _collection_count_sync() -> int:
    with _store_lock:
        return int(_collection.count()) if _collection is not None else 0


def _add_messages_sync(
    records: list[tuple[int, str, str | None, str | None, str, float]],
) -> None:
    if not records:
        return
    with _store_lock:
        if _collection is None:
            return
        contents = [record[1] for record in records]
        embeddings = _embed(contents)
        _collection.upsert(
            ids=[str(record[0]) for record in records],
            embeddings=embeddings,
            metadatas=[
                {
                    "user_qq": record[2] or "",
                    "group_id": record[3] or "",
                    "scene": record[4],
                    "created_at": record[5],
                }
                for record in records
            ],
            documents=[content[:1000] for content in contents],
        )


async def add_message(
    message_id: int,
    content: str,
    user_qq: str | None,
    group_id: str | None,
    scene: str,
    created_at: float,
):
    if not content.strip():
        return
    record = (message_id, content, user_qq, group_id, scene, created_at)
    if _collection is None:
        if _init_task is not None:
            _pending_writes.append(record)
        return
    try:
        await asyncio.to_thread(_add_messages_sync, [record])
    except Exception:
        logger.exception("Failed to add message %d to vector store", message_id)


def _search_similar_sync(
    query: str,
    *,
    scene: str,
    group_id: str | None,
    n_results: int,
    min_time: float,
) -> list[dict]:
    if _collection is None:
        return []
    with _store_lock:
        embedding = _embed([query])
        where_filter = None
        if scene == "group" and group_id:
            where_filter = {
                "$and": [
                    {"scene": {"$eq": "group"}},
                    {"group_id": {"$eq": group_id}},
                ]
            }
        results = _collection.query(
            query_embeddings=embedding,
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

    hits = []
    if results["ids"] and results["ids"][0]:
        for i, _doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            if min_time and meta.get("created_at", 0) < min_time:
                continue
            hits.append(
                {
                    "content": results["documents"][0][i] or "",
                    "user_qq": meta.get("user_qq", ""),
                    "group_id": meta.get("group_id", ""),
                    "created_at": meta.get("created_at", 0),
                    "relevance": round(1 - distance, 3),
                }
            )
    return hits


async def search_similar(
    query: str,
    scene: str,
    group_id: str | None = None,
    user_qq: str | None = None,
    n_results: int = 15,
    min_time: float = 0,
) -> list[dict]:
    """语义搜索相关历史消息。返回 [{"content": ..., "user_qq": ..., "created_at": ...}, ...]"""
    if _collection is None or not query.strip():
        return []
    try:
        return await asyncio.to_thread(
            _search_similar_sync,
            query,
            scene=scene,
            group_id=group_id,
            n_results=n_results,
            min_time=min_time,
        )
    except Exception:
        logger.exception("Vector search failed")
        return []


async def delete_messages(message_ids: list[int] | tuple[int, ...] | set[int]):
    """Remove message embeddings after their source DB rows are compressed/deleted."""
    if _collection is None or not message_ids:
        return
    try:
        ids = [str(mid) for mid in message_ids if mid is not None]
        if ids:
            await asyncio.to_thread(_delete_messages_sync, ids)
            logger.info("Deleted %d messages from vector store", len(ids))
    except Exception:
        logger.exception("Failed to delete messages from vector store")


def _delete_messages_sync(ids: list[str]) -> None:
    if _collection is None:
        return
    with _store_lock:
        _collection.delete(ids=ids)


async def close_vector_store():
    global _init_task, _status
    if _init_task is not None and not _init_task.done():
        _init_task.cancel()
        try:
            await _init_task
        except asyncio.CancelledError:
            pass
    _init_task = None
    _pending_writes.clear()
    await asyncio.to_thread(_clear_vector_store_sync)
    _status = "stopped"


def _clear_vector_store_sync() -> None:
    global _collection, _document_count, _embedding_model
    with _store_lock:
        _collection = None
        _embedding_model = None
        _document_count = 0
