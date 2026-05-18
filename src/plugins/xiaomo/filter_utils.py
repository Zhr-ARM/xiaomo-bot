"""小源 QQ 机器人 - 内容过滤与文本工具

1. 敏感内容过滤
2. 智能文本分段
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import get_config

# ─── 敏感词过滤 ────────────────────────────────────────────────────────────────

# 内置敏感词列表（最小集，用户可在 data/blocklist.txt 扩展）
_DEFAULT_BLOCKLIST = {
    # 政治敏感（示例占位）
    # 低俗内容
    # 暴力内容
}

_blocklist: set[str] | None = None


def _load_blocklist() -> set[str]:
    """加载敏感词列表"""
    global _blocklist
    if _blocklist is not None:
        return _blocklist

    _blocklist = set(_DEFAULT_BLOCKLIST)

    config = get_config()
    blocklist_file = config.get("safety", {}).get("blocklist_file", "data/blocklist.txt")

    # 尝试加载文件
    file_path = Path(blocklist_file)
    if not file_path.is_absolute():
        project_root = Path(__file__).parent.parent.parent.parent
        file_path = project_root / blocklist_file

    if file_path.exists():
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word and not word.startswith("#"):
                    _blocklist.add(word)

    return _blocklist


def check_content_safe(text: str) -> tuple[bool, str | None]:
    """
    检查内容是否安全。
    返回 (is_safe, reason)
    """
    if not get_config().get("safety", {}).get("enabled", True):
        return True, None

    blocklist = _load_blocklist()

    text_lower = text.lower()
    for word in blocklist:
        if word.lower() in text_lower:
            return False, f"包含敏感词: {word}"

    return True, None


def filter_unsafe_response(text: str) -> str:
    """过滤 LLM 回复中的敏感内容"""
    is_safe, reason = check_content_safe(text)
    if not is_safe:
        return "喵呜...这个话题小源不太方便聊呢 (´･ω･`) 换个话题好嘛？"
    return text


# ─── 代码块检测与处理 ─────────────────────────────────────────────────────────


_CODE_BLOCK_PATTERN = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def has_long_code_block(text: str, max_lines: int = 50) -> bool:
    """检测是否有超过 max_lines 行的代码块"""
    for match in _CODE_BLOCK_PATTERN.finditer(text):
        code = match.group(2)
        if len(code.split("\n")) > max_lines:
            return True
    return False


def extract_code_blocks(text: str) -> list[dict]:
    """提取所有代码块"""
    blocks = []
    for match in _CODE_BLOCK_PATTERN.finditer(text):
        blocks.append({
            "language": match.group(1) or "text",
            "code": match.group(2),
        })
    return blocks


# ─── 消息长度裁剪 ─────────────────────────────────────────────────────────────


def trim_to_length(text: str, max_chars: int = 800) -> str:
    """将文本裁剪到合适长度，尽量在句末截断"""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    # 找最后一个句末符号
    for punct in ["。", "！", "？", ".", "!", "?", "\n"]:
        idx = truncated.rfind(punct)
        if idx > max_chars * 0.7:
            return truncated[: idx + 1]

    return truncated.rstrip() + "..."
