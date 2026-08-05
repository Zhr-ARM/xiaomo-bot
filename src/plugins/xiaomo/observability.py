"""Bridge standard-library Xiaomo logs into NoneBot's Loguru sink."""

from __future__ import annotations

import logging

from nonebot import logger as nonebot_logger


class _NoneBotHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname
            nonebot_logger.opt(exception=record.exc_info).log(
                level,
                "[xiaomo.{}] {}",
                record.name.rsplit(".", 1)[-1],
                record.getMessage(),
            )
        except Exception:
            self.handleError(record)


class _SuppressHealthAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "/healthz" not in message and "/readyz" not in message


def configure_logging() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _SuppressHealthAccess) for item in access_logger.filters):
        access_logger.addFilter(_SuppressHealthAccess())

    root = logging.getLogger("xiaomo")
    if any(isinstance(handler, _NoneBotHandler) for handler in root.handlers):
        return
    root.handlers.clear()
    root.addHandler(_NoneBotHandler())
    root.setLevel(logging.INFO)
    root.propagate = False
