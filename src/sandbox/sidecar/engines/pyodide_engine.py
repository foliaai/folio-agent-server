#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : pyodide_engine.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    Python 执行引擎：spawn Node 孙进程驱动 Pyodide（nodejs/runner.js）。
    每次调用全新 runner 进程（冷启动 ~1s 换取最强状态隔离——test_06 跨调用
    状态泄漏验收依赖这一点）。
    资源控制：墙钟 Timer kill + Node --max-old-space-size；
    fuel 计量为 Wasmtime 独有能力，此路径 fuel_used 恒 0（CPU 靠墙钟兜底）。
    网络：用户代码无 sockets（Pyodide 天然），pyodide.http 被 runner 禁用，
    唯一出口 folio_fetch 经 stdin/stdout 转发回本引擎 → HostContext。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import json
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

from loguru import logger

from src.sandbox.contracts import (
    NetEvent,
    SandboxErrorCode,
    SandboxRequest,
    SandboxResult,
)
from src.sandbox.sidecar.engines.base import BaseEngine
from src.sandbox.sidecar.hostfuncs import HostContext


def _linux_address_space_limit(mem_mb: int):
    """Linux：以 RLIMIT_AS 限制 Node 进程虚拟地址空间（wasm 堆增长受此约束）。
    macOS 的 setrlimit(RLIMIT_AS) 是 no-op，开发机退化为墙钟兜底（生产为 Linux）。"""

    def _apply() -> None:
        import resource

        limit = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return _apply if platform.system() == "Linux" else None


# nodejs/ 目录位于 src/sandbox/sidecar/nodejs（本文件在 sidecar/engines/ 下，向上两级）
_NODEJS_DIR = Path(__file__).parent.parent / "nodejs"


class PyodideEngine(BaseEngine):
    """Python 引擎：每请求 spawn 一次 node runner.js"""

    def __init__(self, node_bin: str | None = None):
        self._node_bin = node_bin or os.environ.get("SANDBOX_NODE_BIN", "node")

    def name(self) -> str:
        return "pyodide"

    def supported(self) -> bool:
        if shutil.which(self._node_bin) is None:
            return False
        return (_NODEJS_DIR / "node_modules" / "pyodide").exists()

    def _run(self, request: SandboxRequest) -> SandboxResult:  # noqa: C901
        limits = request.limits
        host = HostContext(request)

        # Node 堆上限独立于 memory_limit_mb：Pyodide 运行时自身常驻 ~150-200MB old space，
        # 用 memory_limit_mb 直配会误杀正常执行；Python 侧的真实限制靠 RLIMIT_AS（Linux）
        # 与 wasm 堆编译期上限（2GB）。64MB 默认档下给 256MB JS 堆余量。
        node_heap_mb = max(256, limits.memory_limit_mb)
        cmd = [self._node_bin, f"--max-old-space-size={node_heap_mb}", "runner.js"]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(_NODEJS_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                preexec_fn=_linux_address_space_limit(limits.memory_limit_mb),
            )
        except OSError as e:
            return self.err(SandboxErrorCode.ENGINE_CRASH, f"failed to spawn node runner: {e}")

        stderr_buf: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for err_line in proc.stderr:  # 防止 stderr 管道积压阻塞 node
                stderr_buf.append(err_line)
                if sum(len(s) for s in stderr_buf) > 64 * 1024:
                    del stderr_buf[:-4]

        drain = threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()

        done_event = threading.Event()
        done_payload: dict | None = None
        timed_out = threading.Event()

        def _on_timeout() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(limits.node_runner_timeout_ms / 1000.0, _on_timeout)
        timer.daemon = True
        timer.start()

        try:
            # 1. 等 runner 就绪（Pyodide 加载）
            if not self._expect_frame(proc, "ready", timeout=limits.wall_clock_timeout_ms / 1000.0 + 5.0):
                return self._fail(proc, stderr_buf, SandboxErrorCode.ENGINE_CRASH, "node runner failed to become ready")

            # 2. 下发任务
            run_frame = {
                "type": "run",
                "id": request.id,
                "code": request.code,
                "files": [{"name": f.name, "content_b64": f.content_b64} for f in request.files],
                "outputLimitBytes": limits.output_limit_mb * 1024 * 1024,
            }
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(run_frame) + "\n")
            proc.stdin.flush()

            # 3. 读 stdout：net 转发 / done 收尾（阻塞循环，超时由 Timer kill 触发 EOF）
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ftype = frame.get("type")
                if ftype == "net":
                    reply = self._handle_net(host, frame)
                    proc.stdin.write(json.dumps(reply) + "\n")
                    proc.stdin.flush()
                elif ftype == "done":
                    done_payload = frame
                    done_event.set()
                    break
                elif ftype == "log":
                    logger.bind(name="sandbox").debug(f"runner: {frame.get('message', '')}")

            if done_payload is None:
                if timed_out.is_set():
                    return self._fail(proc, stderr_buf, SandboxErrorCode.TIMEOUT, "python execution exceeded wall clock")
                return self._fail(proc, stderr_buf, SandboxErrorCode.OOM, "node runner died (likely OOM)")

            # 4. 组装结果
            err = frame_error(done_payload.get("error"))
            result = SandboxResult(
                ok=done_payload.get("ok", False),
                stdout=done_payload.get("stdout", ""),
                stderr=done_payload.get("stderr", ""),
                result=done_payload.get("resultCode"),
                error_code=err[0],
                error_detail=err[1],
                net_events=host.net_events,
            )
            return result
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    # ==================== 内部工具 ====================

    def _expect_frame(self, proc: subprocess.Popen, ftype: str, timeout: float) -> bool:
        """等待指定类型的协议帧（用定时轮询实现超时，超时 kill）"""
        import time as _time

        deadline = _time.monotonic() + timeout
        assert proc.stdout is not None
        while _time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                return False
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if frame.get("type") == ftype:
                return True
        proc.kill()
        return False

    def _handle_net(self, host: HostContext, frame: dict) -> dict:
        """runner 转发来的 folio_fetch 调用 → HostContext 执行 → net-result 回包"""
        seq = frame.get("seq", 0)
        url = frame.get("url", "")
        body = frame.get("body")
        content_type = frame.get("contentType")
        status, resp_body, error = host.http_fetch(url, body, content_type)
        return {
            "type": "net-result",
            "seq": seq,
            "ok": not error,
            "status": status,
            "body": resp_body,
            "error": error,
        }

    def _fail(self, proc: subprocess.Popen, stderr_buf: list[str], code: SandboxErrorCode, detail: str) -> SandboxResult:
        tail = "\n".join(stderr_buf[-5:])[:2000]
        full = f"{detail}" + (f" | node stderr: {tail}" if tail else "")
        return self.err(code, full)


def frame_error(raw: object) -> tuple[SandboxErrorCode | None, str]:
    """runner done 帧的 error 字符串 → (错误码, detail)"""
    if not raw:
        return None, ""
    text = str(raw)
    lowered = text.lower()
    if "memory" in lowered and ("allocat" in lowered or "heap" in lowered or "out of memory" in lowered):
        return SandboxErrorCode.OOM, text
    if "syntaxerror" in lowered or "indentationerror" in lowered:
        return SandboxErrorCode.BAD_CODE, text
    return SandboxErrorCode.INTERNAL, text
