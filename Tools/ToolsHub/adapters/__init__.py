# -*- coding: utf-8 -*-
"""适配器注册表：自动发现 adapters/ 下所有 ToolAdapter 子类。

新增一个工具接入 ToolsHub，只需：
  1) 复制 _template.py 为 <你的工具>.py；
  2) 填好 id / name / default_dirname / default_entry 等；
  3) 重启 Hub（或前端点「重新扫描」）——边栏会自动出现该工具。
不需要修改 Hub 主程序。
"""

import importlib
import pkgutil
import traceback
from pathlib import Path

from .base import ToolAdapter

_REGISTRY: dict[str, type] = {}
_ERRORS: dict[str, str] = {}


def _discover():
    """扫描本包下所有模块，收集 ToolAdapter 子类。"""
    global _REGISTRY, _ERRORS
    registry: dict[str, type] = {}
    errors: dict[str, str] = {}
    pkg_dir = Path(__file__).resolve().parent
    for mod_info in pkgutil.iter_modules([str(pkg_dir)]):
        mod_name = mod_info.name
        if mod_name.startswith("_") or mod_name in ("base",):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{mod_name}")
        except Exception:
            errors[mod_name] = traceback.format_exc(limit=3)
            continue
        for obj in vars(mod).values():
            if (isinstance(obj, type) and issubclass(obj, ToolAdapter)
                    and obj is not ToolAdapter and getattr(obj, "id", "")):
                registry[obj.id] = obj
    _REGISTRY = registry
    _ERRORS = errors


def all_adapters() -> dict[str, type]:
    if not _REGISTRY:
        _discover()
    return _REGISTRY


def reload_adapters() -> dict[str, type]:
    _discover()
    return _REGISTRY


def get(adapter_id: str):
    return all_adapters().get(adapter_id)


def discover_errors() -> dict[str, str]:
    all_adapters()
    return dict(_ERRORS)


__all__ = ["ToolAdapter", "all_adapters", "reload_adapters", "get", "discover_errors"]
