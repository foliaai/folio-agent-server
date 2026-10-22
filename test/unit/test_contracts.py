#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""单元测试：协议契约（帧编解码 / 错误码 / roundtrip）。"""

import json

from src.sandbox.contracts import (
    SandboxErrorCode,
    SandboxLang,
    SandboxRequest,
    SandboxResponse,
    SandboxResult,
    decode_exec_frame,
    encode_frame,
)


def test_exec_frame_roundtrip():
    req = SandboxRequest(
        id="t1", lang=SandboxLang.PYTHON, code="print(1)",
        input="in", meta={"agent_id": "a1"},
    )
    wire = json.loads(encode_frame(req))
    wire["type"] = "exec"
    decoded = decode_exec_frame(json.dumps(wire))
    assert decoded is not None
    assert decoded.id == "t1"
    assert decoded.lang == SandboxLang.PYTHON
    assert decoded.code == "print(1)"
    assert decoded.input == "in"
    assert decoded.meta == {"agent_id": "a1"}


def test_decode_ignores_non_exec_frames():
    assert decode_exec_frame(json.dumps({"type": "ping"})) is None
    assert decode_exec_frame("not json") is None
    assert decode_exec_frame(json.dumps({"type": "exec", "id": "x"})) is None  # 缺必填字段


def test_result_frame_has_type_marker():
    resp = SandboxResponse(id="t2", ok=True, result=SandboxResult(ok=True))
    wire = json.loads(encode_frame(resp))
    assert wire["type"] == "result"
    assert wire["ok"] is True


def test_error_code_values():
    # 协议错误码是稳定性契约：改值等于破坏双端兼容
    assert SandboxErrorCode.FUEL_EXHAUSTED.value == "fuel_exhausted"
    assert SandboxErrorCode.OOM.value == "oom"
    assert SandboxErrorCode.TIMEOUT.value == "timeout"
    assert SandboxErrorCode.DENIED_NET.value == "denied_net"
    assert SandboxErrorCode.OUTPUT_TRUNCATED.value == "output_truncated"
    assert SandboxErrorCode.ENGINE_CRASH.value == "engine_crash"
