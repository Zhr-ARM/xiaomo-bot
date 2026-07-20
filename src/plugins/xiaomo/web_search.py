"""小源 QQ 机器人 - 智能联网搜索 (Tavily Search API)

两级触发：
1. 显式搜索指令：正则匹配（"搜索 xxx"、"百度 xxx" 等）
2. 自然问句：候选匹配 → 直接 Tavily 搜索
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from tavily import AsyncTavilyClient

from .config import get_config

logger = logging.getLogger("xiaomo.web_search")
CST = timezone(timedelta(hours=8))


@dataclass
class SearchResult:
    status: str
    context: str = ""
    query: str = ""
    trigger: str = "none"
    required: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.context)

# ── 显式搜索指令（正则匹配，精准触发，长词在前避免歧义）─────────────
_EXPLICIT_TRIGGERS = [
    r"(?:搜索|搜|查一下|查一查|查查|查)(?:一下|一哈)?\s*(.+)",
    r"上网(?:搜索|搜|查)(?:一下|一哈)?\s*(.+)",
    r"联网(?:搜索|搜|查)(?:一下|一哈)?\s*(.+)",
    r"网络搜索(?:一下|一哈)?\s*(.+)",
    r"网上搜(?:一下|一哈)?\s*(.+)",
    r"(?:google|bing|百度)(?:一下|一哈)?\s*(.+)",
    r"(?:搜|搜索)(?:索)?\s*[:：]\s*(.+)",
]

# ── 自然问句候选（匹配这些模式才会触发搜索，防止滥用）─────────────────
_NATURAL_CANDIDATES = [
    r"(?:现在|最近|今日|今天|昨天|本周|这个月|今年).{2,40}",
    r"(?:最新|最近).{2,30}",
    r"什么是.{4,30}[？?]?$",
    r"(?:怎么|如何).{2,30}[？?]?$",
    r".{4,30}(?:怎么样|如何|怎样)[？?]?$",
    r"有什么.{2,30}",
    r"有没有.{2,30}",
    r"知道.{2,20}吗[？?]?$",
    r".{2,20}(?:版本|更新|发布|新闻|热搜|消息|动态|事件|比赛|情况)",
    r"\S+是什么[？?]?$",
    r"(?:帮我?)?(?:查|查查|查一下|查一查|查了).{2,30}",
]

_SPORTS_COMMENTARY_CUES = (
    "\u9510\u8bc4",
    "\u70b9\u8bc4",
    "\u8bc4\u4ef7",
    "\u5206\u6790",
    "\u600e\u4e48\u770b",
    "\u600e\u4e48\u770b\u5f85",
)

_SPORTS_CONTEXT_TERMS = (
    "\u8db3\u7403",
    "\u4e16\u754c\u676f",
    "\u6bd4\u8d5b",
    "\u6218\u5e73",
    "\u7403\u961f",
    "\u7403\u5458",
    "\u8fb9\u950b",
    "\u524d\u950b",
    "\u4e2d\u573a",
    "\u540e\u536b",
    "\u95e8\u5c06",
    "\u8fdb\u7403",
    "\u9996\u53d1",
    "\u9635\u5bb9",
)


def _looks_like_live_sports_commentary(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 6:
        return False
    return any(cue in t for cue in _SPORTS_COMMENTARY_CUES) and any(
        term in t for term in _SPORTS_CONTEXT_TERMS
    )


def detect_explicit_search(text: str) -> str | None:
    """检测显式搜索指令，返回搜索词或 None"""
    if not text:
        return None
    t = text.strip()
    for pattern in _EXPLICIT_TRIGGERS:
        m = re.search(pattern, t)  # search 而非 match，容忍前缀干扰
        if m:
            query = m.group(1).strip()
            if query and len(query) >= 2:
                return query
    return None


def is_natural_candidate(text: str) -> bool:
    """消息是否匹配自然问句模式（预过滤，减少不必要的 API 调用）"""
    if not text or len(text) < 8:
        return False
    if _looks_like_live_sports_commentary(text):
        return True
    for pattern in _NATURAL_CANDIDATES:
        if re.search(pattern, text.strip()):
            return True
    return False


def _has_search_api_key() -> bool:
    search_cfg = get_config().get("web_search", {})
    return bool(search_cfg.get("api_key") or os.getenv("TAVILY_API_KEY", ""))


async def search_web(
    query: str,
    max_results: int | None = None,
    search_depth: str | None = None,
    include_answer: bool | None = None,
) -> Optional[dict]:
    """调用 Tavily Search API"""
    config = get_config()
    search_cfg = config.get("web_search", {})
    api_key = search_cfg.get("api_key") or os.getenv("TAVILY_API_KEY", "")
    if max_results is None:
        max_results = search_cfg.get("max_results", 5)
    if search_depth is None:
        search_depth = search_cfg.get("search_depth", "basic")
    if include_answer is None:
        include_answer = search_cfg.get("include_answer", True)

    if not api_key:
        logger.warning("Tavily API key not configured")
        return None

    try:
        async with AsyncTavilyClient(api_key=api_key) as client:
            response = await client.search(
                query=query,
                max_results=max_results,
                search_depth=search_depth,
                include_answer=include_answer,
                topic="general",
            )
            logger.info(
                "Web search OK: '%s' -> %d results, %.2fs",
                query[:60],
                len(response.get("results", [])),
                response.get("response_time", 0),
            )
            return response
    except Exception:
        logger.exception("Web search error: %s", query[:60])
        return None


def _optimize_query(text: str) -> str:
    """优化中文搜索词，让 Tavily 返回更相关的结果。

    对热搜/新闻/实时类查询加上当前年份，提高时效性。
    对"热搜""排行榜"类查询补充中文搜索关键词。
    """
    now = datetime.now(CST)
    year = str(now.year)

    t = text.strip()

    # 热搜/排行榜/新闻类 → 加年份 + 中文语境关键词
    if any(kw in t for kw in ["热搜", "排行", "热门", "热榜", "头条"]):
        platform_hint = ""
        if any(kw in t for kw in ["b站", "B站", "bilibili", "Bilibili"]):
            platform_hint = " bilibili 哔哩哔哩"
        elif any(kw in t for kw in ["微博", "微薄"]):
            platform_hint = " 微博"
        elif any(kw in t for kw in ["抖音", "douyin"]):
            platform_hint = " 抖音"
        elif any(kw in t for kw in ["知乎"]):
            platform_hint = " 知乎"
        return f"{year} {t}{platform_hint}"

    # 新闻/最新/实时类 → 加年份
    if any(kw in t for kw in ["新闻", "最新", "今日", "今天", "最近", "刚刚", "新版本"]):
        if year not in t:
            return f"{year} {t}"

    if _looks_like_live_sports_commentary(t):
        prefix = f"{year} \u8db3\u7403"
        if "\u4e16\u754c\u676f" not in t:
            prefix += " \u4e16\u754c\u676f"
        return f"{prefix} {t}"

    return t


def format_search_results(data: dict, max_content_chars: int = 800) -> str:
    """将搜索结果格式化为 LLM 上下文文本"""
    if not data:
        return ""

    parts = [
        "[以下信息来自实时网络搜索，你必须以此为准回答。",
        "如果搜索结果与用户问题**明显不相关或完全无关**，"
        "诚实告诉用户「喵…搜索到的内容和你想问的不太一样，可能关键词有问题，换个说法试试？」。",
        "不要假装搜索结果回答了用户的问题，不要编造信息。]",
    ]

    answer = data.get("answer", "")
    if answer:
        parts.append(f"搜索摘要：{answer}")

    results = data.get("results", [])
    if results:
        parts.append("相关网页：")
        for i, r in enumerate(results[:5], 1):
            title = r.get("title", "无标题")
            url = r.get("url", "")
            content = r.get("content", "")
            if len(content) > max_content_chars:
                content = content[:max_content_chars] + "..."
            parts.append(f"[{i}] {title}\n    URL: {url}\n    内容: {content}")

    return "\n".join(parts)


async def run_smart_search_result(clean_text: str) -> SearchResult:
    """智能搜索主入口：两级触发，返回带状态的搜索结果。"""
    search_cfg = get_config().get("web_search", {})
    max_results = search_cfg.get("max_results", 5)
    search_depth = search_cfg.get("search_depth", "basic")

    # 第 1 级：显式搜索指令（正则，零延迟）
    query = detect_explicit_search(clean_text)
    if query:
        optimized = _optimize_query(query)
        logger.info("[Search] Explicit: '%s' -> '%s'", query[:60], optimized[:80])
        data = await search_web(
            optimized,
            search_depth=search_depth,
            max_results=max_results,
        )
        if data:
            context = format_search_results(data)
            if context:
                return SearchResult(
                    status="ok",
                    context=context,
                    query=optimized,
                    trigger="explicit",
                    required=True,
                )
        if not _has_search_api_key():
            return SearchResult(
                status="not_configured",
                query=optimized,
                trigger="explicit",
                required=True,
                reason="Tavily API key not configured",
            )
        return SearchResult(
            status="no_results",
            query=optimized,
            trigger="explicit",
            required=True,
            reason="search provider returned no usable data",
        )

    # 第 2 级：自然问句 → 直接搜索
    if not search_cfg.get("natural_query", True):
        return SearchResult(status="disabled", reason="natural query disabled")
    if not is_natural_candidate(clean_text):
        return SearchResult(status="not_triggered")

    optimized = _optimize_query(clean_text)
    logger.info("[Search] Natural: '%s' -> '%s'", clean_text[:60], optimized[:80])
    data = await search_web(
        optimized,
        search_depth=search_depth,
        max_results=max_results,
    )
    if data:
        context = format_search_results(data)
        if context:
            return SearchResult(
                status="ok",
                context=context,
                query=optimized,
                trigger="natural",
                required=True,
            )

    if not _has_search_api_key():
        return SearchResult(
            status="not_configured",
            query=optimized,
            trigger="natural",
            required=True,
            reason="Tavily API key not configured",
        )
    return SearchResult(
        status="no_results",
        query=optimized,
        trigger="natural",
        required=True,
        reason="search provider returned no usable data",
    )


async def run_smart_search(clean_text: str) -> str | None:
    """Backward-compatible wrapper that returns only formatted context."""
    result = await run_smart_search_result(clean_text)
    return result.context or None
