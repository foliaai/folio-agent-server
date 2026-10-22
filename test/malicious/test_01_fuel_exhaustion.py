#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 1/7：资源耗尽攻击（死循环）。

ADR-0001 验收语义：死循环必须被确定性中断，服务不被打垮。
- JS 路径：fuel 确定性计量。注意 wasmtime-py 49 上游 bug——fuel 耗尽触发
  C API panic（非优雅 trap），sidecar 进程死亡；manager 自动重启，调用方
  收到 ENGINE_CRASH。验收断言落在本质：**请求有界返回 + 池自愈 + 后续可用**。
- Python 路径：无指令计量，墙钟 Timer kill 优雅返回 timeout。
"""

import time

import pytest

from src.sandbox.contracts import SandboxErrorCode, SandboxLang, SandboxLimits
from test.conftest import wasm_available


def test_python_infinite_loop_killed_by_wall_clock(sandbox):
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="while True: pass",
        limits=SandboxLimits(wall_clock_timeout_ms=2500, node_runner_timeout_ms=4000),
    )
    assert not r.ok
    assert r.error_code == SandboxErrorCode.TIMEOUT
    assert r.wall_ms < 15_000  # 远小于外层兜底，说明墙钟精确生效


@pytest.mark.skipif(not wasm_available("fuel_bomb"), reason="wasm 编译链不可用")
def test_js_infinite_loop_fuel_exhaustion_pool_self_heals(sandbox, wasm_b64):
    # fuel 上限 3e7：足够组件初始化（~1e7）但死循环很快耗尽
    r = sandbox.execute(
        lang=SandboxLang.JS,
        code=wasm_b64("fuel_bomb"),
        limits=SandboxLimits(fuel_per_call=30_000_000),
        meta={"agent_id": "malicious-01"},
    )
    # 上游 bug 语义：panic → ENGINE_CRASH（若未来 wasmtime 修复则 FUEL_EXHAUSTED）
    assert not r.ok
    assert r.error_code in (SandboxErrorCode.FUEL_EXHAUSTED, SandboxErrorCode.ENGINE_CRASH)
    assert r.wall_ms < 60_000

    # 关键验收：资源攻击后池自愈，正常请求立即可用（给 respawn 一点时间）
    healed = False
    for attempt in range(8):
        time.sleep(1.0)
        probe = sandbox.execute(lang=SandboxLang.PYTHON, code='result = "alive"')
        if probe.ok and probe.result == "alive":
            healed = True
            break
    assert healed, "sidecar pool did not self-heal after fuel exhaustion crash"
