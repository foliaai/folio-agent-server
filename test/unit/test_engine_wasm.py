#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""引擎级测试：坏 wasm 组件的错误映射（防 selfdestruct 误伤回归）。

背景：曾把 instantiate 失败映射为 ENGINE_CRASH（fatal），坏组件直接触发
sidecar 自杀（rc=3）——坏输入是用户问题（BAD_CODE），不是进程异常。
"""

import base64

from src.sandbox.contracts import SandboxErrorCode, SandboxLang, SandboxLimits, SandboxRequest
from src.sandbox.sidecar.engines.wasmtime_engine import WasmtimeEngine

_E = WasmtimeEngine()


def test_garbage_bytes_is_bad_code():
    req = SandboxRequest(
        id="t", lang=SandboxLang.JS,
        code=base64.b64encode(b"this is not wasm at all").decode(),
    )
    r = _E.execute(req)
    assert not r.ok
    assert r.error_code == SandboxErrorCode.BAD_CODE


def test_non_component_wasm_is_bad_code():
    # 合法的 base64 但内容不是 wasm 组件
    req = SandboxRequest(
        id="t", lang=SandboxLang.JS,
        code=base64.b64encode(b"\x00asm\x01\x00\x00\x00").decode(),  # wasm magic 但非组件
    )
    r = _E.execute(req)
    assert not r.ok
    assert r.error_code in (SandboxErrorCode.BAD_CODE, SandboxErrorCode.ENGINE_CRASH)


def test_echo_component_runs():
    import os

    path = "/tmp/sbx-echo.wasm"
    if not os.path.exists(path):
        import pytest

        pytest.skip("echo sample not built")
    req = SandboxRequest(
        id="t", lang=SandboxLang.JS,
        code=base64.b64encode(open(path, "rb").read()).decode(),
        input="engine-level",
        limits=SandboxLimits(memory_limit_mb=64),
    )
    r = _E.execute(req)
    assert r.ok and r.result == "echo: engine-level"
    assert r.fuel_used > 1_000_000  # 真实计量
