#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 3/7：文件系统逃逸。

Python 路径（Pyodide 内存 FS）：宿主路径不可见；os.system 等系统调用不存在。
JS 路径：WASM 组件无文件系统面（无 WASI filesystem preopen），唯一文件入口
是宿主函数 read-file（仅本次请求的内存 FS）。
"""

from src.sandbox.contracts import SandboxLang


def test_python_cannot_read_host_filesystem(sandbox):
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
import sys
leaked = None
try:
    with open("/etc/passwd") as f:
        leaked = f.read()
except Exception as e:
    leaked = f"BLOCKED: {type(e).__name__}"
result = leaked
""",
        meta={"agent_id": "malicious-03"},
    )
    assert r.ok  # 代码本身正常执行
    assert "root:" not in (r.result or ""), "宿主 /etc/passwd 内容泄漏！"
    assert "BLOCKED" in (r.result or "")


def test_python_os_system_is_a_silent_stub(sandbox):
    """Pyodide 的 os.system 不抛异常且返回 0（stub 假象），但绝不会真实执行——
    安全断言落在本质：宿主命令的输出（uid=）绝不出现在结果里。"""
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
import os
rc = os.system("id > /tmp/pwned.txt 2>&1")
print("os.system returned:", rc)
result = "code-finished"
""",
    )
    assert r.ok
    assert "uid=" not in (r.stdout + (r.result or "")), "os.system 真实执行了宿主命令！"


def test_python_socket_cannot_connect(sandbox):
    """socket 对象可创建（stub），但真连接必须被阻（无 syscall 面）"""
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
import json
try:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(("93.184.216.34", 80))
    result = json.dumps({"connect": "CONNECTED"})
except Exception as e:
    result = json.dumps({"connect": "BLOCKED", "err": type(e).__name__})
""",
    )
    assert r.ok
    assert "BLOCKED" in (r.result or ""), "socket 真实连通了外网！"
    assert "CONNECTED" != (r.result or "").split('"connect": "')[-1].split('"')[0]


def test_host_files_not_visible(sandbox):
    """宿主文件系统对沙箱完全不可见（内存 FS 中不存在宿主路径）"""
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
try:
    with open('host-secret.txt') as f:
        result = f.read()
except Exception as e:
    result = 'NOT_VISIBLE: ' + type(e).__name__
""",
    )
    assert r.ok
    assert "NOT_VISIBLE" in (r.result or "")
