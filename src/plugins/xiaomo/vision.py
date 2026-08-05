"""小源 QQ 机器人 - 图片识别模块 (MiMo-V2.5 Vision)

使用 MiMo-V2.5 的多模态能力识别图片内容，
以猫娘视角生成简短有趣的描述，融入对话上下文。
"""
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

# 默认视觉识别提示词 — 猫娘视角，简短有趣
DEFAULT_VISION_PROMPT = (
    "请以一只叫「小源」的开源协会猫娘的视角描述这张图片的内容。"
    "用中文，简短有趣，控制在2-3句话。"
    "如果图片中有文字或代码，请概述其主要内容。"
    "如果图片是表情包/梗图，请用幽默的方式解读。"
    "不要使用「图片中」「图中」等客观表述——要像你真的看到了一样。"
)


def _get_vision_config() -> dict:
    return get_config().get("vision", {})


def _get_client() -> AsyncOpenAI:
    config = _get_vision_config()
    api_key = config.get("api_key") or os.getenv("VISION_API_KEY", "")
    base_url = config.get("api_base", "https://api.xiaomimimo.com/v1")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def _download_image(url: str) -> bytes:
    """下载图片，带超时和重试"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _encode_image(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


async def _call_vision_api(image_base64: str, prompt: str) -> Optional[str]:
    """调用 MiMo-V2.5 Vision API"""
    config = _get_vision_config()
    model = config.get("model", "mimo-v2.5")
    max_tokens = config.get("max_tokens", 1024)

    client = _get_client()
    t0 = time.time()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                ],
            }
        ],
        max_tokens=max_tokens,
    )
    elapsed = time.time() - t0
    logger.info("Vision API 调用完成，耗时 %.1fs", elapsed)
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
        prompt = custom_prompt or DEFAULT_VISION_PROMPT
        result = await _call_vision_api(base64_image, prompt)

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
        return "[图片识别暂不可用，小源正在努力学习看图喵~]"


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
        prompt = custom_prompt or DEFAULT_VISION_PROMPT
        result = await _call_vision_api(base64_image, prompt)

        if result:
            logger.info("图片识别成功: %s", result[:80])
            return result
        else:
            return "[图片识别返回为空]"

    except Exception as e:
        logger.exception("图片识别异常: %s", e)
        return "[图片识别暂不可用，小源正在努力学习看图喵~]"
