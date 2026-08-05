"""HTTP liveness and QQ-bridge readiness endpoints."""

from __future__ import annotations

import time

from fastapi.responses import JSONResponse
from nonebot import get_bots, get_driver

_started_at = time.time()


def _semantic_memory_status() -> dict[str, object]:
    from .vector_store import get_vector_status

    return get_vector_status()


async def _healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "alive",
            "qq_connected": bool(get_bots()),
            "semantic_memory": _semantic_memory_status(),
            "uptime_seconds": round(time.time() - _started_at, 1),
        }
    )


async def _readyz() -> JSONResponse:
    bots = get_bots()
    return JSONResponse(
        {
            "status": "ready" if bots else "waiting_for_qq_bridge",
            "qq_connected": bool(bots),
            "bot_ids": sorted(str(bot_id) for bot_id in bots),
            "semantic_memory": _semantic_memory_status(),
        },
        status_code=200 if bots else 503,
    )


def install_health_routes() -> None:
    app = getattr(get_driver(), "server_app", None)
    if app is None or getattr(app.state, "xiaomo_health_installed", False):
        return
    app.add_api_route("/healthz", _healthz, methods=["GET"], include_in_schema=False)
    app.add_api_route("/readyz", _readyz, methods=["GET"], include_in_schema=False)
    app.state.xiaomo_health_installed = True
