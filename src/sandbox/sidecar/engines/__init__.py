#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : __init__.py（engines）
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    引擎注册与分发：lang → engine 实例。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Optional

from src.sandbox.contracts import SandboxLang, SandboxRequest, SandboxResult
from src.sandbox.sidecar.engines.base import BaseEngine

_ENGINES: dict[SandboxLang, BaseEngine] = {}


def _ensure_engine(lang: SandboxLang) -> Optional[BaseEngine]:
    """懒初始化引擎（wasmtime 常驻；pyodide 每请求 spawn 无需常驻状态）"""
    if lang in _ENGINES:
        return _ENGINES[lang]

    if lang == SandboxLang.JS:
        from src.sandbox.sidecar.engines.wasmtime_engine import WasmtimeEngine

        engine = WasmtimeEngine()
    elif lang == SandboxLang.PYTHON:
        from src.sandbox.sidecar.engines.pyodide_engine import PyodideEngine

        engine = PyodideEngine()
    else:  # pragma: no cover —— SandboxLang 只有两值
        return None

    if not engine.supported():
        return None
    _ENGINES[lang] = engine
    return engine


def dispatch(request: SandboxRequest) -> SandboxResult:
    """按请求语言分发执行；引擎不存在/不可用时返回 ENGINE_CRASH（带说明）"""
    from src.sandbox.contracts import SandboxErrorCode

    engine = _ensure_engine(request.lang)
    if engine is None:
        return SandboxResult(
            ok=False,
            error_code=SandboxErrorCode.ENGINE_CRASH,
            error_detail=f"engine for lang={request.lang.value} unavailable (node/pyodide not installed?)",
        )
    return engine.execute(request)


def available_engines() -> list[str]:
    """sidecar 启动宣告用：探测各引擎可用性"""
    langs = []
    for lang in SandboxLang:
        probe = _ensure_engine(lang)
        if probe is not None:
            langs.append(lang.value)
    return langs
