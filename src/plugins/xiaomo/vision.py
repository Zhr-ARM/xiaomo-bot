"""小源 QQ 机器人 - 图片识别模块 (MiMo-V2.5 Vision)"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI

from .config import get_config

logger = logging.getLogger("xiaomo.vision")


def _get_vision_config() -> dict:
    return get_config().get("vision", {})


def _get_client() -> AsyncOpenAI:
    config = _get_vision_config()
    api_key = config.get("api_key") or os.getenv("VISION_API_KEY", "")
    base_url = config.get("api_base", "https://api.xiaomimimo.com/v1")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def _download_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _encode_image(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


async def _call_vision_api(image_base64: str, prompt: str) -> Optional[str]:
    config = _get_vision_config()
    model = config.get("model", "mimo-v2.5")
    max_tokens = config.get("max_tokens", 1024)

    client = _get_client()
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
        max_completion_tokens=max_tokens,
    )
    return response.choices[0].message.content


async def describe_image_from_url(image_url: str) -> Optional[str]:
    config = _get_vision_config()
    max_size = config.get("max_image_size_mb", 20) * 1024 * 1024

    try:
        image_data = await _download_image(image_url)
        if len(image_data) > max_size:
            return f"[图片过大 ({len(image_data)/1024/1024:.1f}MB)]"

        base64_image = _encode_image(image_data)
        prompt = (
            "请以一只猫娘的视角描述这张图片的内容。"
            "用中文，简短有趣，控制在3句话以内。"
        )
        return await _call_vision_api(base64_image, prompt)

    except httpx.HTTPError as e:
        logger.error("Failed to download image: %s", e)
        return "[图片下载失败]"
    except Exception as e:
        logger.exception("Image recognition failed: %s", e)
        return "[图片识别暂不可用，小源正在努力学习看图喵~]"


async def describe_image_from_file(file_path: str) -> Optional[str]:
    config = _get_vision_config()
    max_size = config.get("max_image_size_mb", 20) * 1024 * 1024

    try:
        path = Path(file_path)
        if not path.exists():
            return "[图片文件不存在]"

        data = path.read_bytes()
        if len(data) > max_size:
            return f"[图片过大 ({len(data)/1024/1024:.1f}MB)]"

        base64_image = _encode_image(data)
        prompt = (
            "请以一只猫娘的视角描述这张图片，"
            "简短有趣，控制在3句话以内。"
        )
        return await _call_vision_api(base64_image, prompt)

    except Exception as e:
        logger.exception("Local image recognition failed: %s", e)
        return "[图片识别暂不可用]"
