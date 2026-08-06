from __future__ import annotations

from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from src.plugins.xiaomo import handlers, vision


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"GIF89a" + b"x" * 20, "image/gif"),
        (b"\x89PNG\r\n\x1a\n" + b"x" * 20, "image/png"),
        (b"\xff\xd8\xff" + b"x" * 20, "image/jpeg"),
        (b"RIFF\x10\x00\x00\x00WEBP" + b"x" * 20, "image/webp"),
    ],
)
def test_detect_image_mime_uses_magic_bytes(payload, expected):
    assert vision._detect_image_mime(payload) == expected


@pytest.mark.asyncio
async def test_vision_request_preserves_real_mime_and_short_budget(monkeypatch):
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="这是一个委屈的卡通表情。"),
                    )
                ]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setattr(vision, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        vision,
        "get_config",
        lambda: {
            "vision": {
                "model": "vision-test",
                "max_tokens": 384,
                "temperature": 0.1,
            }
        },
    )

    result = await vision._call_vision_api(
        "ZmFrZQ==",
        "describe",
        mime_type="image/gif",
    )

    assert result == "这是一个委屈的卡通表情。"
    assert calls[0]["max_tokens"] == 384
    assert calls[0]["temperature"] == 0.1
    image_url = calls[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/gif;base64,")


@pytest.mark.asyncio
async def test_describe_bytes_passes_detected_gif_mime(monkeypatch):
    captured = {}

    async def fake_call(image_base64, prompt, *, mime_type):
        captured["mime_type"] = mime_type
        captured["prompt"] = prompt
        return "这是一个动图表情。"

    monkeypatch.setattr(vision, "_call_vision_api", fake_call)
    monkeypatch.setattr(
        vision,
        "get_config",
        lambda: {"vision": {"max_image_size_mb": 20}},
    )

    result = await vision.describe_image_from_bytes(b"GIF89a" + b"x" * 200)

    assert result == "这是一个动图表情。"
    assert captured["mime_type"] == "image/gif"
    assert "不角色扮演" in captured["prompt"]


@pytest.mark.asyncio
async def test_recognize_images_reuses_cached_description(monkeypatch):
    async def network_must_not_run(*_args, **_kwargs):
        raise AssertionError("cached image description should be reused")

    monkeypatch.setattr(vision, "describe_image_from_url", network_must_not_run)
    monkeypatch.setattr(vision, "describe_image_from_bytes", network_must_not_run)
    monkeypatch.setattr(
        handlers,
        "get_config",
        lambda: {"vision": {"max_images_per_turn": 2}},
    )

    descriptions, image_url, first_description = await handlers._recognize_images(
        [
            {
                "url": "https://example.test/reaction.gif",
                "file": "reaction.gif",
                "description": "这是一个捂脸苦恼的卡通表情。",
            }
        ],
        bot=SimpleNamespace(),
    )

    assert descriptions == ["[图片内容描述：这是一个捂脸苦恼的卡通表情。]"]
    assert image_url == "https://example.test/reaction.gif"
    assert first_description == "这是一个捂脸苦恼的卡通表情。"
