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


def configure_logging() -> None:
    root = logging.getLogger("xiaomo")
    if any(isinstance(handler, _NoneBotHandler) for handler in root.handlers):
        return
    root.handlers.clear()
    root.addHandler(_NoneBotHandler())
    root.setLevel(logging.INFO)
    root.propagate = False
