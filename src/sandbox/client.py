#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : client.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    SandboxClient：业务侧（工具层/agent loop）调用沙箱的唯一入口。
    职责：请求组装（limits 钳制）、池化调度、审计落盘、异常归一化。
    用法：
        result = await get_sandbox_client().execute(
            lang=SandboxLang.PYTHON, code="print(1)", meta={"agent_id": "a1"})
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import uuid
from typing import Dict, List, Optional

from loguru import logger

from src.sandbox.audit import SandboxAuditLogger
from src.sandbox.contracts import (
    SandboxErrorCode,
    SandboxFile,
    SandboxLang,
    SandboxLimits,
    SandboxRequest,
    SandboxResult,
)
from src.sandbox.limits import clamp_limits, limits_from_config
from src.sandbox.manager import SidecarCrashedError, SidecarManager


class SandboxClient:
    """沙箱客户端（持有进程池与审计器）"""

    def __init__(
        self,
        manager: SidecarManager,
        platform_limits: SandboxLimits,
        audit: SandboxAuditLogger,
    ):
        self._manager = manager
        self._platform = platform_limits
        self._audit = audit

    async def execute(
        self,
        lang: SandboxLang,
        code: str,
        input: str = "",
        files: Optional[List[SandboxFile]] = None,
        allowlist: Optional[List[str]] = None,
        limits: Optional[SandboxLimits] = None,
        meta: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        """执行一段沙箱代码；任何进程层异常都归一化为 SandboxResult 错误码"""
        effective = clamp_limits(limits or SandboxLimits(), self._platform)
        request = SandboxRequest(
            id=uuid.uuid4().hex[:12],
            lang=lang,
            code=code,
            input=input,
            files=files or [],
            limits=effective,
            allowlist=allowlist or [],
            meta=meta or {},
        )

        # 外层等待 = 引擎墙钟 + Pyodide 孙进程缓冲 + 协议开销
        outer_timeout = effective.wall_clock_timeout_ms / 1000.0 + effective.node_runner_timeout_ms / 1000.0 + 10.0

        try:
            sp = await self._manager.acquire(timeout_s=outer_timeout)
            async with sp.lock:
                response = await sp.execute(request, timeout_s=outer_timeout)
            result = response.result
        except SidecarCrashedError as e:
            result = SandboxResult(
                ok=False,
                error_code=SandboxErrorCode.ENGINE_CRASH,
                error_detail=f"sidecar crashed during execution (upstream panic or OOM kill): {e}",
            )
        except TimeoutError as e:
            result = SandboxResult(
                ok=False, error_code=SandboxErrorCode.TIMEOUT, error_detail=f"no sidecar available: {e}"
            )
        except Exception as e:  # noqa: BLE001 —— 协议边界归一化
            logger.bind(name="sandbox").error(f"sandbox client error: {e}")
            result = SandboxResult(
                ok=False, error_code=SandboxErrorCode.INTERNAL, error_detail=f"{type(e).__name__}: {e}"
            )

        self._audit.log_execution(request, result)
        return result


# ==================== 模块级单例 ====================

_client: Optional[SandboxClient] = None


def get_sandbox_client() -> SandboxClient:
    """获取共享沙箱客户端（须先 start_sandbox 完成初始化）"""
    if _client is None:
        raise RuntimeError("sandbox not started; call start_sandbox() in app lifespan first")
    return _client


async def start_sandbox(
    sandbox_config: Optional[dict] = None,
    audit_dir: str = "logs/sandbox",
) -> SandboxClient:
    """应用 lifespan 启动入口：构建池 + 审计器，幂等"""
    global _client
    if _client is not None:
        return _client

    from src.utils.env_manager import get_env_manager

    env = get_env_manager()
    cfg = sandbox_config or {}
    platform = limits_from_config(cfg)
    pool_size = int(cfg.get("sidecar_pool_size", 2))

    manager = SidecarManager(pool_size=pool_size)
    await manager.start()
    audit = SandboxAuditLogger(audit_dir=env.get_sandbox_audit_dir() or audit_dir)
    _client = SandboxClient(manager=manager, platform_limits=platform, audit=audit)
    logger.bind(name="sandbox").info(f"sandbox started: pool={pool_size} stats={manager.stats}")
    return _client


async def stop_sandbox() -> None:
    """应用 lifespan 关闭入口"""
    global _client
    if _client is None:
        return
    await _client._manager.stop()
    _client = None
    logger.bind(name="sandbox").info("sandbox stopped")
