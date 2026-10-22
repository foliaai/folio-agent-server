#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : server.py（sidecar）
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    sidecar 主循环：stdin 逐行读协议帧 → 分发执行 → stdout 单行回帧。
    M1 为串行处理（一次一个请求；并发由 API 侧进程池横向扩）。
    stdout 是协议通道——引擎与第三方库的日志必须走 stderr 或 loguru 文件 sink。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import sys

from loguru import logger

from src.sandbox.contracts import (
    SandboxErrorCode,
    SandboxRequest,
    SandboxResponse,
    SandboxResult,
    decode_exec_frame,
    decode_line,
    encode_frame,
)
from src.sandbox.sidecar import engines
from src.sandbox.sidecar.selfdestruct import SelfDestructTracker

# 默认值与 config.toml [sandbox] sidecar_max_failures 对齐（sidecar 独立进程读不到 config 时兜底）
_MAX_FAILURES = 3


def run(max_failures: int = _MAX_FAILURES) -> None:
    """主循环：阻塞读 stdin，EOF（父进程关闭）即正常退出"""
    tracker = SelfDestructTracker(max_failures)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        data = decode_line(line)
        if data is None:
            continue

        ftype = data.get("type")

        if ftype == "ping":
            sys.stdout.write(encode_frame_from({"type": "pong"}) + "\n")
            sys.stdout.flush()
            continue

        if ftype != "exec":
            continue

        request = decode_exec_frame(line)
        if request is None:
            _emit(
                SandboxResponse(
                    id="unknown",
                    ok=False,
                    result=SandboxResult(
                        ok=False, error_code=SandboxErrorCode.BAD_CODE, error_detail="malformed exec frame"
                    ),
                )
            )
            continue

        result = engines.dispatch(request)
        _emit(SandboxResponse(id=request.id, ok=result.ok, result=result))

        if result.ok:
            tracker.record_success()
        else:
            tracker.record_failure(result.error_code, result.error_detail)

    logger.bind(name="sandbox").info("sidecar stdin closed, exiting")


def _emit(response: SandboxResponse) -> None:
    sys.stdout.write(encode_frame(response) + "\n")
    sys.stdout.flush()


def encode_frame_from(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
