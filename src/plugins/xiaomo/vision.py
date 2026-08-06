"""小源 QQ 机器人 - 图片识别模块 (MiMo-V2.5 Vision)."""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI

from .config import get_config

logger = logging.getLogger("xiaomo.vision")

# 视觉层只负责提取事实，语气和角色由最终回复模型统一处理。
DEFAULT_VISION_PROMPT = (
    "准确识别这张图片，直接用中文概括可见内容。"
    "有文字就读出关键文字，有代码或报错就说明关键问题。"
    "只写1到2句，不自我介绍、不角色扮演、不添加看不到的信息。"
)

_client: AsyncOpenAI | None = None


def _get_vision_config() -> dict:
    return get_config().get("vision", {})


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client
    config = _get_vision_config()
    api_key = config.get("api_key") or os.getenv("VISION_API_KEY", "")
    base_url = config.get("api_base", "https://api.xiaomimimo.com/v1")
    _client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(config.get("timeout_seconds", 25)),
        max_retries=int(config.get("max_retries", 0)),
    )
    return _client


async def _download_image(url: str) -> bytes:
    """下载图片。"""
    timeout = float(_get_vision_config().get("download_timeout_seconds", 12))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _encode_image(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _detect_image_mime(data: bytes) -> str:
    """Detect common image formats from magic bytes instead of trusting QQ suffixes."""
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    logger.warning("无法从文件头判断图片格式，按 JPEG 兼容处理")
    return "image/jpeg"


async def _call_vision_api(
    image_base64: str,
    prompt: str,
    *,
    mime_type: str = "image/jpeg",
) -> Optional[str]:
    """调用 MiMo-V2.5 Vision API"""
    config = _get_vision_config()
    model = config.get("model", "mimo-v2.5")
    max_tokens = int(config.get("max_tokens", 384))

    client = _get_client()
    t0 = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    },
                ],
            }
        ],
        max_tokens=max_tokens,
        temperature=float(config.get("temperature", 0.1)),
    )
    elapsed = time.perf_counter() - t0
    finish_reason = response.choices[0].finish_reason
    logger.info(
        "Vision API 调用完成，耗时 %.1fs，finish=%s",
        elapsed,
        finish_reason,
    )
    if finish_reason == "length":
        logger.warning("Vision API 输出达到 token 上限")
    return response.choices[0].message.content


async def describe_image_from_url(
    image_url: str,
    custom_prompt: str | None = None,
) -> Optional[str]:
    """从 URL 下载图片并生成猫娘视角的描述

    Args:
        image_url: 图片的 HTTP(S) URL
        custom_prompt: 自定义提示词，不传则使用默认猫娘视角提示词

    Returns:
        图片描述文本，失败时返回以 [图片 开头的错误提示
    """
    config = _get_vision_config()
    max_size = config.get("max_image_size_mb", 20) * 1024 * 1024

    try:
        t0 = time.time()
        image_data = await _download_image(image_url)
        download_time = time.time() - t0
        logger.info("图片下载完成，大小 %.1fKB，耗时 %.1fs",
                     len(image_data) / 1024, download_time)

        if len(image_data) > max_size:
            size_mb = len(image_data) / 1024 / 1024
            logger.warning("图片过大 (%.1fMB)，跳过识别", size_mb)
            return f"[图片过大 ({size_mb:.1f}MB)]"

        base64_image = _encode_image(image_data)
        mime_type = _detect_image_mime(image_data)
        prompt = custom_prompt or DEFAULT_VISION_PROMPT
        result = await _call_vision_api(base64_image, prompt, mime_type=mime_type)

        if result:
            logger.info("图片识别成功: %s", result[:80])
            return result
        else:
            return "[图片识别返回为空]"

    except httpx.TimeoutException:
        logger.error("图片下载超时: %s", image_url)
        return "[图片下载超时]"
    except httpx.HTTPError as e:
        logger.error("图片下载失败 (HTTP %s): %s",
                     getattr(e, 'response', None) and e.response.status_code, e)
        return "[图片下载失败]"
    except Exception as e:
        logger.exception("图片识别异常: %s", e)
        return "[图片识别暂不可用]"


async def describe_image_from_file(
    file_path: str,
    custom_prompt: str | None = None,
) -> Optional[str]:
    """从本地文件读取图片并生成猫娘视角的描述

    Args:
        file_path: 本地图片文件路径
        custom_prompt: 自定义提示词，不传则使用默认猫娘视角提示词

    Returns:
        图片描述文本，失败时返回以 [图片 开头的错误提示
    """
    config = _get_vision_config()
    max_size = config.get("max_image_size_mb", 20) * 1024 * 1024

    try:
        path = Path(file_path)
        if not path.exists():
            return "[图片文件不存在]"
        size = path.stat().st_size
        if size > max_size:
            size_mb = size / 1024 / 1024
            logger.warning("本地图片过大 (%.1fMB)，跳过识别", size_mb)
            return f"[图片过大 ({size_mb:.1f}MB)]"

        data = path.read_bytes()
        return await describe_image_from_bytes(data, custom_prompt)

    except Exception as e:
        logger.exception("本地图片识别失败: %s", e)
        return "[图片识别暂不可用]"


async def describe_image_from_bytes(
    image_data: bytes,
    custom_prompt: str | None = None,
) -> Optional[str]:
    """从内存中的图片数据生成猫娘视角的描述

    Args:
        image_data: 图片原始字节数据
        custom_prompt: 自定义提示词，不传则使用默认猫娘视角提示词

    Returns:
        图片描述文本，失败时返回以 [图片 开头的错误提示
    """
    config = _get_vision_config()
    max_size = config.get("max_image_size_mb", 20) * 1024 * 1024

    try:
        if len(image_data) > max_size:
            size_mb = len(image_data) / 1024 / 1024
            logger.warning("图片过大 (%.1fMB)，跳过识别", size_mb)
            return f"[图片过大 ({size_mb:.1f}MB)]"

        if len(image_data) < 100:
            logger.warning("图片数据过小 (%d bytes)，可能无效", len(image_data))
            return "[图片数据无效]"

        base64_image = _encode_image(image_data)
        mime_type = _detect_image_mime(image_data)
        prompt = custom_prompt or DEFAULT_VISION_PROMPT
        result = await _call_vision_api(base64_image, prompt, mime_type=mime_type)

        if result:
            logger.info("图片识别成功: %s", result[:80])
            return result
        else:
            return "[图片识别返回为空]"

    except Exception as e:
        logger.exception("图片识别异常: %s", e)
        return "[图片识别暂不可用]"
