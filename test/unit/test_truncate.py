#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""单元测试：输出截断机制（BaseEngine._truncate，JS/Python 引擎共用）。"""

from src.sandbox.contracts import SandboxErrorCode, SandboxLimits, SandboxResult
from src.sandbox.sidecar.engines.base import BaseEngine


class _Engine(BaseEngine):
    def name(self):
        return "test"

    def supported(self):
        return True

    def _run(self, request):
        return SandboxResult(ok=True)


def _truncate(stdout: str = "", stderr: str = "", result: str | None = None, limit_mb: int = 1):
    r = SandboxResult(ok=True, stdout=stdout, stderr=stderr, result=result)
    BaseEngine._truncate(r, SandboxLimits(output_limit_mb=limit_mb))
    return r


def test_stdout_truncated_with_marker():
    r = _truncate(stdout="A" * (3 * 1024 * 1024))
    assert len(r.stdout.encode()) <= 1024 * 1024 + 200
    assert "output truncated" in r.stdout
    assert r.error_code == SandboxErrorCode.OUTPUT_TRUNCATED


def test_result_truncated_with_marker():
    r = _truncate(result="B" * (2 * 1024 * 1024))
    assert len(r.result) <= 1024 * 1024 + 200
    assert r.error_code == SandboxErrorCode.OUTPUT_TRUNCATED


def test_stderr_truncated():
    r = _truncate(stderr="E" * (2 * 1024 * 1024))
    assert len(r.stderr) <= 1024 * 1024 + 200


def test_small_output_untouched():
    r = _truncate(stdout="hello", result="world")
    assert r.stdout == "hello" and r.result == "world"
    assert r.error_code is None


def test_truncate_is_utf8_safe():
    """截断点落在多字节字符中间时不产生解码崩溃（errors=ignore）"""
    r = _truncate(stdout="中" * (600 * 1024))  # 1.8MB UTF-8
    assert "output truncated" in r.stdout
