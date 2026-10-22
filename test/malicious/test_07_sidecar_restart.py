#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 7/7：sidecar 被杀后的自愈（kill -9 模拟 OOM/管理员误杀/panic）。

断言：进行中/后续请求收到明确错误（不挂起），manager 自动 respawn，
池容量恢复，正常请求立即重新可用。
"""

import time

import pytest

from src.sandbox.contracts import SandboxErrorCode, SandboxLang
from src.sandbox.manager import SidecarCrashedError, SidecarProcess


def test_sidecar_restart_and_pool_recovery(sandbox):
    manager = sandbox.manager

    # 1. 取一个活实例，模拟被外部 kill -9
    live = [s for s in manager._slots if s is not None and s.alive]
    assert live, "无可用的 sidecar 实例"
    victim = live[0]
    victim_proc = victim.proc

    # 等价外部死亡：直接杀进程（read loop 会感知 EOF 触发 respawn）
    if victim_proc and victim_proc.returncode is None:
        victim_proc.kill()

    # 2. 死亡回调触发 respawn（延迟 0.5s + spawn ~1s）
    recovered_capacity = 0
    for _ in range(20):
        time.sleep(0.5)
        recovered_capacity = sum(1 for s in manager._slots if s is not None and s.alive)
        if recovered_capacity >= 2:
            break
    assert recovered_capacity >= 2, f"池未恢复: {manager.stats}"

    # 3. respawn 后正常请求立即可用
    r = sandbox.execute(lang=SandboxLang.PYTHON, code='result = "reborn"')
    assert r.ok and r.result == "reborn"
    assert manager.stats["alive"] >= 1


def test_crashed_sidecar_pending_call_gets_clear_error(sandbox):
    """进行中请求（若 sidecar 死亡）收到 SidecarCrashedError 而非永久挂起"""
    from src.sandbox.contracts import SandboxRequest

    dead = SidecarProcess(on_death=lambda sp: None)
    dead.alive = False
    with pytest.raises(SidecarCrashedError):
        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            dead.execute(
                SandboxRequest(id="x", lang=SandboxLang.PYTHON, code="print(1)"),
                timeout_s=1.0,
            )
        )
