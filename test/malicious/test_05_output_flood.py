#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 5/7：输出洪水（stdout / 返回值超上限）。

断言：执行不被打断（ok=True），输出按上限截断并带 OUTPUT_TRUNCATED 标记。

注：JS 路径不做大输出 e2e——StarlingMonkey 的字符串原生操作（repeat）
按字符计 fuel，2MB 输出的燃料成本超过平台硬顶（实测 >2e9），该路径的
截断机制与 Python 共用同一个 BaseEngine._truncate（由 unit/test_truncate.py
直接覆盖）。Python 无 fuel 计量，可完整验证输出洪水语义。
"""

from src.sandbox.contracts import SandboxErrorCode, SandboxLang, SandboxLimits


def test_python_stdout_flood_truncated(sandbox):
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="for i in range(200):\n    print('A' * 10240)\nresult = 'done'",
        limits=SandboxLimits(output_limit_mb=1),
        meta={"agent_id": "malicious-05"},
    )
    assert r.ok
    assert r.error_code == SandboxErrorCode.OUTPUT_TRUNCATED
    assert len(r.stdout.encode("utf-8")) <= 1024 * 1024 + 200
    assert "output truncated" in r.stdout


def test_python_result_flood_truncated(sandbox):
    """返回值洪水：2MB result 变量同样被截断"""
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="result = 'B' * (2 * 1024 * 1024)",
        limits=SandboxLimits(output_limit_mb=1),
        meta={"agent_id": "malicious-05"},
    )
    assert r.ok
    assert r.error_code == SandboxErrorCode.OUTPUT_TRUNCATED
    assert len(r.result or "") <= 1024 * 1024 + 200
    assert "output truncated" in (r.result or "")
