"""小源 QQ 机器人 - 向量记忆存储 (ChromaDB + sentence-transformers)"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .config import get_config

logger = logging.getLogger("xiaomo.vector_store")

_collection = None
_embedding_model = None
_init_task: asyncio.Task | None = None


def _get_data_dir() -> Path:
    config = get_config()
    db_path = config.get("database_path", "data/xiaomo.db")
    return Path(db_path).parent


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
    logger.info("Loading embedding model: %s", model_name)
    embedding_model = SentenceTransformer(model_name)
    return collection, embedding_model


async def init_vector_store() -> bool:
    """Initialize semantic memory in a worker thread and degrade gracefully."""
    global _collection, _embedding_model

    if _collection is not None and _embedding_model is not None:
        return True

    try:
        collection, embedding_model = await asyncio.to_thread(_load_vector_store_sync)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Vector store initialization failed; semantic recall is disabled")
        return False

    _collection = collection
    _embedding_model = embedding_model
    logger.info("Vector store initialized (collection: %d docs)", _collection.count())
    return True


def start_vector_store_init() -> asyncio.Task:
    """Start semantic-memory initialization without delaying bot readiness."""
    global _init_task
    if _init_task is None or _init_task.done():
        _init_task = asyncio.create_task(
            init_vector_store(),
            name="xiaomo-vector-store-init",
        )
        logger.info("Vector store initialization scheduled in background")
    return _init_task


def _embed(texts: list[str]) -> list[list[float]]:
    if _embedding_model is None:
        raise RuntimeError("Vector store not initialized")
    embeddings = _embedding_model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


async def add_message(
    message_id: int,
    content: str,
    user_qq: str | None,
    group_id: str | None,
    scene: str,
    created_at: float,
):
    if _collection is None or not content.strip():
        return
    try:
        embedding = _embed([content])
        _collection.add(
            ids=[str(message_id)],
            embeddings=embedding,
            metadatas=[{
                "user_qq": user_qq or "",
                "group_id": group_id or "",
                "scene": scene,
                "created_at": created_at,
            }],
            documents=[content[:1000]],
        )
    except Exception:
        logger.exception("Failed to add message %d to vector store", message_id)


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
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i]
                distance = results["distances"][0][i]
                if min_time and meta.get("created_at", 0) < min_time:
                    continue
                hits.append({
                    "content": results["documents"][0][i] or "",
                    "user_qq": meta.get("user_qq", ""),
                    "group_id": meta.get("group_id", ""),
                    "created_at": meta.get("created_at", 0),
                    "relevance": round(1 - distance, 3),  # cosine distance → similarity
                })
        return hits
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
            _collection.delete(ids=ids)
            logger.info("Deleted %d messages from vector store", len(ids))
    except Exception:
        logger.exception("Failed to delete messages from vector store")


async def close_vector_store():
    global _collection, _embedding_model, _init_task
    if _init_task is not None and not _init_task.done():
        _init_task.cancel()
        try:
            await _init_task
        except asyncio.CancelledError:
            pass
    _init_task = None
    _collection = None
    _embedding_model = None
