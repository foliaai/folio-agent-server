#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : manager.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    SidecarManager：sidecar 进程池（API 服务器侧）。
    - spawn python -m src.sandbox.sidecar（asyncio subprocess，行协议）
    - 每实例单飞（asyncio.Lock）：一个 sidecar 同时只处理一个请求
    - 崩溃检测（stdout EOF / 非零退出）：pending 请求收到 SidecarCrashedError，
      槽位自动重启（fuel 耗尽会触发 wasmtime C API panic 杀死 sidecar，
      这是上游已知行为，重启兜底）
    - 优雅关闭：drain 后 terminate
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
import sys
from typing import Optional

from loguru import logger

from src.sandbox.contracts import (
    SandboxRequest,
    SandboxResponse,
    decode_line,
)


class SidecarCrashedError(RuntimeError):
    """sidecar 进程在处理期间死亡（panic/被杀/OOM）"""


class SidecarProcess:
    """单个 sidecar 实例：spawn、读写循环、单飞锁"""

    _seq = 0

    def __init__(self, on_death):
        SidecarProcess._seq += 1
        self.name = f"sidecar-{SidecarProcess._seq}"
        self.on_death = on_death
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()  # 单飞：一次一个请求
        self._ready = asyncio.Event()
        self.engines: list[str] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self.alive = False

    async def start(self) -> None:
        # limit：协议帧含 stdout/result，可达数 MB——asyncio StreamReader 默认 64KB
        # 行上限会在大帧上抛 LimitOverrunError，误判 sidecar 死亡
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "src.sandbox.sidecar",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # sidecar 日志不混入协议通道
            limit=32 * 1024 * 1024,
        )
        self.alive = True
        # stderr 消费循环：防管道积压阻塞 sidecar，同时给崩溃诊断留痕（曾因 rc=1 无 stderr 排查困难）
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._reader_task = asyncio.create_task(self._read_loop())
        # 等 ready 帧（引擎探测完成），超时视为启动失败
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30.0)
            logger.bind(name="sandbox").info(f"{self.name} ready, engines={self.engines}")
        except asyncio.TimeoutError:
            await self._kill()
            raise RuntimeError(f"{self.name} failed to become ready in 30s")

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.bind(name="sandbox").debug(f"{self.name} stderr: {text}")
        except asyncio.CancelledError:
            pass

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                data = decode_line(line.decode("utf-8", errors="replace").strip())
                if data is None:
                    continue
                ftype = data.get("type")
                if ftype == "ready":
                    self.engines = data.get("engines", [])
                    self._ready.set()
                elif ftype == "result":
                    fut = self._pending.pop(data.get("id", ""), None)
                    if fut is not None and not fut.done():
                        fut.set_result(SandboxResponse.model_validate(data))
        except asyncio.CancelledError:
            pass
        finally:
            await self._handle_death()

    async def _handle_death(self) -> None:
        """stdout EOF 或读循环异常：sidecar 已死，failed 所有 pending"""
        self.alive = False
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(SidecarCrashedError(f"{self.name} died during execution"))
        self._pending.clear()
        logger.bind(name="sandbox").warning(f"{self.name} exited (rc={self.proc.returncode if self.proc else '?'})")
        self.on_death(self)

    async def execute(self, request: SandboxRequest, timeout_s: float) -> SandboxResponse:
        """单飞执行一个请求（调用方应持有 self.lock）"""
        if not self.alive or self.proc is None or self.proc.stdin is None:
            raise SidecarCrashedError(f"{self.name} not alive")

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request.id] = fut
        try:
            frame = request.model_dump()
            frame["type"] = "exec"
            self.proc.stdin.write((__import__("json").dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            # sidecar 串行执行模型下超时意味着引擎卡死（epoch/kill 机制失效）——重启该实例
            logger.bind(name="sandbox").error(f"{self.name} timed out, killing")
            await self._kill()
            raise
        finally:
            self._pending.pop(request.id, None)

    async def _kill(self) -> None:
        self.alive = False
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()

    async def stop(self) -> None:
        """优雅关闭：关闭 stdin → 等待退出 → 强杀兜底"""
        if self.proc and self.proc.stdin and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                await self._kill()
        self.alive = False


class SidecarManager:
    """sidecar 进程池：固定大小、轮询取空闲、死亡自动重启"""

    def __init__(self, pool_size: int = 2, audit_logger=None):
        self._pool_size = max(1, pool_size)
        self._audit = audit_logger
        self._slots: list[Optional[SidecarProcess]] = []
        self._rr = 0  # 轮询游标
        self._started = False
        self._respawn_failures = 0  # 连续 respawn 失败计数（crash-loop 熔断）

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self._pool_size):
            try:
                sp = await self._spawn()
                self._slots.append(sp)
            except Exception as e:  # noqa: BLE001 —— 单槽启动失败不阻断整体
                logger.bind(name="sandbox").error(f"sidecar slot {i} failed to start: {e}")
                self._slots.append(None)

    async def _spawn(self) -> SidecarProcess:
        sp = SidecarProcess(on_death=self._on_death)
        await sp.start()
        return sp

    def _on_death(self, dead: SidecarProcess) -> None:
        """槽位重填（延迟 spawn，避免 crash-loop 打满 CPU；连续失败达到阈值后熔断冷却）"""
        if not self._started:
            return

        async def _respawn() -> None:
            await asyncio.sleep(0.5)
            # crash-loop 熔断：连续 5 次 respawn 失败 → 冷却 60s（依赖缺失等环境问题不该刷日志刷 CPU）
            if self._respawn_failures >= 5:
                logger.bind(name="sandbox").error(
                    f"respawn circuit broken ({self._respawn_failures} consecutive failures), cooling down 60s"
                )
                await asyncio.sleep(60)
                self._respawn_failures = 0
            try:
                fresh = await self._spawn()
                self._respawn_failures = 0
                for i, cur in enumerate(self._slots):
                    if cur is dead:
                        self._slots[i] = fresh
                        break
                else:
                    self._slots.append(fresh)
                if self._audit:
                    self._audit.log_sidecar_event("respawned", {"name": dead.name})
            except Exception as e:  # noqa: BLE001
                self._respawn_failures += 1
                logger.bind(name="sandbox").error(f"respawn failed ({self._respawn_failures}): {e}")

        asyncio.create_task(_respawn())

    async def acquire(self, timeout_s: float = 30.0) -> SidecarProcess:
        """轮询获取一个存活且空闲的 sidecar（全部忙时等待）"""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            live = [s for s in self._slots if s is not None and s.alive]
            if not live:
                if asyncio.get_event_loop().time() > deadline:
                    raise RuntimeError("no live sidecar available")
                await asyncio.sleep(0.2)
                continue
            # 轮询找锁未占用的实例（返回后调用方再加锁；竞态下最多重复排队，单飞语义不受影响）
            for _ in range(len(live)):
                idx = self._rr % len(live)
                self._rr += 1
                candidate = live[idx]
                if not candidate.lock.locked():
                    return candidate
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("all sidecars busy")
            await asyncio.sleep(0.1)

    async def stop(self) -> None:
        self._started = False
        for s in self._slots:
            if s is not None:
                await s.stop()
        self._slots.clear()

    @property
    def stats(self) -> dict:
        live = sum(1 for s in self._slots if s is not None and s.alive)
        return {"pool_size": self._pool_size, "alive": live, "busy": sum(1 for s in self._slots if s and s.lock.locked())}
