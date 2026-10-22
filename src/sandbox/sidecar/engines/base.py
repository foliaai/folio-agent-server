#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : base.py（engines）
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    沙箱执行引擎抽象基类：输出截断、错误映射、耗时计量的公共实现。
    引擎协议：execute(request) -> SandboxResult；引擎不得抛异常出协议边界。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import time
import traceback
from abc import ABC, abstractmethod

from loguru import logger

from src.sandbox.contracts import (
    SandboxErrorCode,
    SandboxLimits,
    SandboxRequest,
    SandboxResult,
)


class BaseEngine(ABC):
    """引擎基类：子类实现 _run()，公共层负责截断/错误映射/计时"""

    @abstractmethod
    def name(self) -> str:
        """引擎名（日志用）"""

    @abstractmethod
    def supported(self) -> bool:
        """引擎当前可用性（如 Pyodide 需 node + pyodide 安装就绪）"""

    def execute(self, request: SandboxRequest) -> SandboxResult:
        """执行入口：任何异常都在此收敛为错误码，绝不外抛"""
        started = time.monotonic()
        try:
            result = self._run(request)
        except Exception as e:  # noqa: BLE001 —— 协议边界要求全捕获
            logger.bind(name="sandbox").error(f"engine {self.name()} crashed: {e}\n{traceback.format_exc()}")
            result = SandboxResult(
                ok=False,
                error_code=SandboxErrorCode.ENGINE_CRASH,
                error_detail=f"{type(e).__name__}: {e}",
            )
        result.wall_ms = int((time.monotonic() - started) * 1000)
        self._truncate(result, request.limits)
        return result

    @abstractmethod
    def _run(self, request: SandboxRequest) -> SandboxResult:
        """子类实现：执行代码并返回结果（ok 时必须填充 stdout/result）"""

    # ==================== 公共工具 ====================

    @staticmethod
    def _truncate(result: SandboxResult, limits: SandboxLimits) -> None:
        """stdout/stderr/result 按上限截断（UTF-8 字节计），截断即标记 OUTPUT_TRUNCATED"""
        limit = limits.output_limit_mb * 1024 * 1024

        def cut(text: str) -> str:
            raw = text.encode("utf-8")
            if len(raw) <= limit:
                return text
            return raw[:limit].decode("utf-8", errors="ignore") + "\n[folio-sandbox: output truncated]"

        truncated = (
            len(result.stdout.encode("utf-8")) > limit
            or len(result.stderr.encode("utf-8")) > limit
            or len((result.result or "").encode("utf-8")) > limit
        )
        if truncated:
            result.stdout = cut(result.stdout)
            result.stderr = cut(result.stderr)
            result.result = cut(result.result) if result.result is not None else None
            if result.ok:
                # 执行成功但输出超限：保持 ok=True，同时标记截断（调用方可见）
                result.error_code = SandboxErrorCode.OUTPUT_TRUNCATED
                result.error_detail = "stdout/result exceeded output limit and was truncated"

    @staticmethod
    def err(code: SandboxErrorCode, detail: str = "") -> SandboxResult:
        return SandboxResult(ok=False, error_code=code, error_detail=detail)
