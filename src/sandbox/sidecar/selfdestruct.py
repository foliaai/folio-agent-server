#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : selfdestruct.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    sidecar 自杀监督（ADR-0001 进程拓扑细则）：
    连续 N 次调用失败 / 致命引擎错误 → 本进程立即以非零码退出，
    由 API 侧 SidecarManager 负责拉起新实例（调用方收到明确错误而非挂起）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import os
import threading
from typing import List

from loguru import logger

from src.sandbox.contracts import SandboxErrorCode

# 这些错误码意味着引擎/进程层可能已不稳定（区别于用户代码自身的 fuel/OOM/越权）
_FATAL_CODES = {
    SandboxErrorCode.ENGINE_CRASH,
}


class SelfDestructTracker:
    """连续失败计数（线程安全；成功一次即清零）"""

    def __init__(self, max_failures: int = 3):
        self._max_failures = max_failures
        self._lock = threading.Lock()
        self._consecutive: int = 0
        self._reasons: List[str] = []

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._reasons.clear()

    def record_failure(self, error_code: SandboxErrorCode | None, detail: str) -> None:
        """
        只有进程层致命错误（ENGINE_CRASH）才计入连续失败——
        用户代码的错误（fuel/timeout/oom/bad_code/denied_*）是正常业务语义，
        计入会把高失败率用户的正常流量打崩池子（恶意样本测试曾实证此问题）。
        """
        fatal = error_code in _FATAL_CODES
        if not fatal:
            self.record_success()
            return
        with self._lock:
            self._consecutive += 1
            self._reasons.append(f"{error_code.value if error_code else 'unknown'}: {detail[:200]}")
            count = self._consecutive
            reasons = list(self._reasons)
        if fatal or count >= self._max_failures:
            self.suicide(reasons)

    def suicide(self, reasons: List[str]) -> None:
        """自杀退出（os._exit 跳过清理，最快让出资源；manager 侧负责重启）"""
        logger.bind(name="sandbox").error(f"sidecar self-destruct: {reasons[-3:]}")
        os._exit(3)
