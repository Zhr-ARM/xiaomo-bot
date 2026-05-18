"""小源 QQ 机器人 - 配置管理"""
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_env(value: str) -> str:
    """解析 ${VAR:-default} 格式的环境变量引用"""

    def replacer(match):
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.getenv(var, default)
        return os.getenv(expr, "")

    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def _walk_resolve(obj: Any) -> Any:
    """递归解析配置中的环境变量"""
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_resolve(v) for v in obj]
    return obj


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _walk_resolve(raw)


_config: dict | None = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> dict:
    global _config
    _config = None
    return get_config()
