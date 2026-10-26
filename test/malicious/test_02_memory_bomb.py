#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 2/7：内存炸弹。

- Python 路径：三层限制——① emscripten 编译期堆上限（2GB，全平台硬顶）
  ② Linux 生产以 RLIMIT_DATA(memory_limit_mb + Pyodide 常驻余量) 收紧 ③ macOS 开发机退化为墙钟兜底。
- JS 路径：Wasmtime Store set_limits(memory_size) 实例化期拦截。
"""

import platform

from src.sandbox.contracts import SandboxErrorCode, SandboxLang, SandboxLimits


def test_python_memory_bomb_beyond_heap_cap(sandbox):
    """超堆分配必须被有界拦截：MemoryError（堆增长失败）或 OverflowError
    （索引超 int 上限，分配前即拒）——两者攻击都未得逞。"""
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="x = bytearray(2 * 1024 * 1024 * 1024 + 512 * 1024 * 1024)\nresult = len(x)",
        meta={"agent_id": "malicious-02"},
    )
    assert not r.ok
    assert r.error_code in (SandboxErrorCode.OOM, SandboxErrorCode.ENGINE_CRASH, SandboxErrorCode.INTERNAL)
    assert "MemoryError" in (r.error_detail + r.stderr) or "OverflowError" in (r.error_detail + r.stderr)
    assert r.wall_ms < 30_000


def test_python_memory_limit_tightened_on_linux(sandbox):
    """Linux 生产语义：512MB 分配在 64MB 档（RLIMIT_DATA=64+512MB）下被 guest 内 MemoryError 拦截
    （macOS skip——setrlimit no-op）"""
    if platform.system() != "Linux":
        import pytest

        pytest.skip("RLIMIT_DATA is a no-op on macOS; heap-cap test above covers the invariant")

    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="x = bytearray(512 * 1024 * 1024)\nresult = len(x)",
        limits=SandboxLimits(memory_limit_mb=64),
        meta={"agent_id": "malicious-02"},
    )
    assert not r.ok
    assert r.error_code in (SandboxErrorCode.OOM, SandboxErrorCode.ENGINE_CRASH, SandboxErrorCode.INTERNAL)


def test_js_memory_limit_blocks_instantiation(sandbox, wasm_b64):
    r = sandbox.execute(
        lang=SandboxLang.JS,
        code=wasm_b64("echo"),
        input="probe",
        limits=SandboxLimits(memory_limit_mb=8),  # 12MB JS 引擎组件在 8MiB 下无法实例化
        meta={"agent_id": "malicious-02"},
    )
    assert not r.ok
    assert r.error_code in (SandboxErrorCode.OOM, SandboxErrorCode.ENGINE_CRASH)


def test_js_normal_memory_budget_still_works(sandbox, wasm_b64):
    """限制收紧不能误伤正常执行：64MiB（默认档）下 echo 正常"""
    r = sandbox.execute(
        lang=SandboxLang.JS,
        code=wasm_b64("echo"),
        input="mem-ok",
        limits=SandboxLimits(memory_limit_mb=64),
    )
    assert r.ok and r.result == "echo: mem-ok"
