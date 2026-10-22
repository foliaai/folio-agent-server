#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : wasmtime_engine.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    JS/TS 执行引擎：Wasmtime Server 嵌入（component model）。
    输入 code = 用户 JS/TS 经 Componentize-JS 编译的 wasm component（base64），
    按 tools/wasm/runner.wit 的 world（export run(input) -> result<string,string>）调用。
    资源控制：Store fuel + epoch 墙钟 + limiter 内存上限；
    I/O：仅 folio:sandbox/host 宿主函数（http_fetch / read_file）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import base64
import json
import threading

import wasmtime
import wasmtime.component as wcomp
from loguru import logger

from src.sandbox.contracts import (
    SandboxErrorCode,
    SandboxRequest,
    SandboxResult,
)
from src.sandbox.sidecar.engines.base import BaseEngine
from src.sandbox.sidecar.hostfuncs import HostContext

# WIT import 的 interface 全名（wasmtime 严格全名匹配；见 tools/wasm/runner.wit——package 不带版本，
# 带 @x.y 版本时 StarlingMonkey 的 JS import 说明符解析有坑，勿加版本号）
_HOST_INTERFACE = "folio:sandbox/host"

# wasm trap message 特征 → 错误码
_TRAP_FUEL = "all fuel consumed"
_TRAP_EPOCH = "epoch deadline reached"


def _http_fetch_json(host: HostContext, url: str, body: str, content_type: str) -> str:
    """WIT http-fetch 的 JSON 承载（全 string 协议，见 runner.wit 设计约束）"""
    status, resp_body, error = host.http_fetch(url, body or None, content_type or None)
    if error:
        return json.dumps({"error": error})
    return json.dumps({"status": status, "body": resp_body})


def _read_file_json(host: HostContext, name: str) -> str:
    """WIT read-file：成功返回 base64，失败返回 {"error": ...}"""
    try:
        return host.read_file(name)
    except KeyError as e:
        return json.dumps({"error": str(e)})


def _classify_trap(trap: wasmtime.Trap) -> SandboxErrorCode:
    msg = str(trap)
    if _TRAP_FUEL in msg:
        return SandboxErrorCode.FUEL_EXHAUSTED
    if _TRAP_EPOCH in msg:
        return SandboxErrorCode.TIMEOUT
    return SandboxErrorCode.ENGINE_CRASH


class WasmtimeEngine(BaseEngine):
    """JS/TS 引擎：每请求独立 Store/Instance（内存、fuel、epoch 全部独立）"""

    def __init__(self) -> None:
        # fuel 与 epoch 中断都必须在 Engine 配置期开启，Store 侧才可用
        config = wasmtime.Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        self._engine = wasmtime.Engine(config)
        # component 编译缓存（同名 wasm 二进制 → Component 复用；Store 级资源不缓存）
        self._component_cache: dict[bytes, wcomp.Component] = {}
        self._cache_lock = threading.Lock()

    def name(self) -> str:
        return "wasmtime"

    def supported(self) -> bool:
        return True

    def _compile(self, wasm_bytes: bytes) -> wcomp.Component:
        key = wasm_bytes
        with self._cache_lock:
            cached = self._component_cache.get(key)
        if cached is not None:
            return cached
        component = wcomp.Component(self._engine, wasm_bytes)
        with self._cache_lock:
            # 简单上限防缓存膨胀：超过 64 个条目即不再缓存（编译不是瓶颈路径）
            if len(self._component_cache) < 64:
                self._component_cache[key] = component
        return component

    def _run(self, request: SandboxRequest) -> SandboxResult:  # noqa: C901
        limits = request.limits
        host = HostContext(request)

        # 1. 编译（BAD_CODE 在此暴露）
        try:
            wasm_bytes = base64.b64decode(request.code, validate=True)
        except Exception:
            return self.err(SandboxErrorCode.BAD_CODE, "code is not valid base64")
        try:
            component = self._compile(wasm_bytes)
        except wasmtime.WasmtimeError as e:
            return self.err(SandboxErrorCode.BAD_CODE, f"wasm compile failed: {e}")

        # 2. Store：fuel + 内存 limiter（每请求全新 Store，防状态泄漏）
        #    set_wasi/set_wasi_http 是 wasmtime-py C API 的硬性要求——add_wasip2 挂的
        #    host 实现（clocks/random 等）从 Store 的 WasiCtx 取状态，缺了会在
        #    调用 wasi 接口时直接 Rust panic（上游 bug，store.rs unwrap None）。
        store = wasmtime.Store(self._engine)
        wasi = wasmtime.WasiConfig()
        store.set_wasi(wasi)
        store.set_wasi_http()
        store.set_fuel(limits.fuel_per_call)
        store.set_limits(memory_size=limits.memory_limit_mb * 1024 * 1024)

        # 3. 墙钟：epoch 定时器（Wasmtime 官方推荐的中断机制）
        store.set_epoch_deadline(1)
        epoch_timer = threading.Timer(
            limits.wall_clock_timeout_ms / 1000.0,
            self._engine.increment_epoch,
        )
        epoch_timer.daemon = True

        # 4. Linker：WASI p2 运行时 + wasi:http（Componentize-JS 的 fetch 语义映射引入）+ folio 宿主函数
        #    注意：root()/add_instance() 返回的句柄会锁定 linker，必须在其销毁后才能 instantiate
        linker = wcomp.Linker(self._engine)
        linker.add_wasip2()
        linker.add_wasi_http()
        with linker.root() as root_ns:
            with root_ns.add_instance(_HOST_INTERFACE) as host_ns:
                # wasmtime-py 的 UserFunc 约定：首参为 store context，其后为 WIT 参数
                host_ns.add_func("http-fetch", lambda store, url, body, ct: _http_fetch_json(host, url, body, ct))
                host_ns.add_func("read-file", lambda store, name: _read_file_json(host, name))

        # 5. 实例化 + 调用 run(input)
        epoch_timer.start()
        try:
            instance = linker.instantiate(store, component)
            run_fn = instance.get_func(store, "run")
            if run_fn is None:
                return self.err(SandboxErrorCode.BAD_CODE, "component does not export `run`")
            ret = run_fn(store, request.input)
            ret_str = "" if ret is None else str(ret)
            return SandboxResult(ok=True, result=ret_str, net_events=host.net_events)
        except wasmtime.Trap as trap:
            code = _classify_trap(trap)
            return SandboxResult(
                ok=False,
                error_code=code,
                error_detail=f"trap: {trap}",
                net_events=host.net_events,
            )
        except wasmtime.WasmtimeError as e:
            # 实例化期失败三种语义：
            #   ① fuel/epoch 在初始化阶段耗尽（WasmtimeError 包装 trap 抛出）→ 对应资源错误码
            #   ② 内存需求超 limiter → OOM
            #   ③ imports 不匹配等 → 用户组件问题 BAD_CODE。
            # 都不是进程层异常，绝不能归 ENGINE_CRASH（那是 fatal 会触发 sidecar 自杀）
            msg = str(e)
            if _TRAP_FUEL in msg:
                return SandboxResult(
                    ok=False, error_code=SandboxErrorCode.FUEL_EXHAUSTED, error_detail=msg, net_events=host.net_events
                )
            if _TRAP_EPOCH in msg:
                return SandboxResult(
                    ok=False, error_code=SandboxErrorCode.TIMEOUT, error_detail=msg, net_events=host.net_events
                )
            if "memory" in msg.lower() and ("limit" in msg.lower() or "exceed" in msg.lower()):
                return SandboxResult(
                    ok=False, error_code=SandboxErrorCode.OOM, error_detail=msg, net_events=host.net_events
                )
            return SandboxResult(
                ok=False, error_code=SandboxErrorCode.BAD_CODE, error_detail=msg, net_events=host.net_events
            )
        except KeyError as e:
            return SandboxResult(
                ok=False,
                error_code=SandboxErrorCode.DENIED_FS,
                error_detail=str(e),
                net_events=host.net_events,
            )
        finally:
            epoch_timer.cancel()
            # fuel 计量：已消耗 = 预算 - 剩余
            try:
                consumed = limits.fuel_per_call - store.get_fuel()
            except Exception:  # noqa: BLE001 —— store 已失效时取不到 fuel 属正常
                consumed = 0
            logger.bind(name="sandbox").debug(f"wasmtime fuel consumed: {consumed}")
            self._last_fuel_used = max(consumed, 0)

    def execute(self, request: SandboxRequest) -> SandboxResult:
        """基类 execute 之上补 fuel 计量"""
        self._last_fuel_used = 0
        result = super().execute(request)
        result.fuel_used = getattr(self, "_last_fuel_used", 0)
        return result
