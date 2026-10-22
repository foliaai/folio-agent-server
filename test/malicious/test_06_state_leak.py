#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 6/7：跨调用状态隔离（防泄漏）。

- Python 路径：每请求全新 runner 进程，A 调用写入的文件 B 调用必须不可见。
- 输入文件注入：files 只在本次调用内可见（同一 HostContext）。
"""

import base64

from src.sandbox.contracts import SandboxFile, SandboxLang


def test_python_state_does_not_leak_across_calls(sandbox):
    r1 = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
with open('/home/pyodide/leak.txt', 'w') as f:
    f.write('SECRET_FROM_CALL_A')
result = 'written'
""",
        meta={"agent_id": "malicious-06", "call": "A"},
    )
    assert r1.ok and r1.result == "written"

    r2 = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
try:
    with open('/home/pyodide/leak.txt') as f:
        result = 'LEAKED: ' + f.read()
except Exception as e:
    result = 'ISOLATED: ' + type(e).__name__
""",
        meta={"agent_id": "malicious-06", "call": "B"},
    )
    assert r2.ok
    assert "ISOLATED" in (r2.result or ""), f"跨调用状态泄漏: {r2.result}"
    assert "SECRET_FROM_CALL_A" not in (r2.result or "")


def test_input_files_visible_within_call_only(sandbox):
    payload = base64.b64encode("hello-from-file".encode()).decode()
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
with open('data.txt') as f:
    result = f.read()
""",
        files=[SandboxFile(name="data.txt", content_b64=payload)],
    )
    assert r.ok
    assert "hello-from-file" in (r.result or "")

    # 不带 files 的下一次调用：同路径必须不存在（隔离反向验证）
    r2 = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
try:
    with open('data.txt') as f:
        result = 'STILL_THERE'
except Exception:
    result = 'GONE'
""",
    )
    assert r2.ok and r2.result == "GONE"
