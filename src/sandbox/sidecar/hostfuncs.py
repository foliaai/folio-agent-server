#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : hostfuncs.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    沙箱宿主函数（sidecar 侧唯一 I/O 通道）：
      - http_fetch：allowlist（默认全拒）+ SSRF 双重检查 + 尺寸限制 + 审计事件收集
      - read_file：内存 FS 读取（调用结束即丢弃，防跨调用状态泄漏）
    安全不变量：本模块内不做任何 allowlist 放宽；所有网络访问必须留下 NetEvent。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import base64
import io
import ssl
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

from src.sandbox.allowlist import check_url, resolve_and_check
from src.sandbox.contracts import NetEvent, SandboxLimits, SandboxRequest

# DNS 解析后的二次检查仍走系统 socket；SSL 不验证可配置证书的场景（内网自签）不在 M1 范围
_SSL_CONTEXT: Optional[ssl.SSLContext] = ssl.create_default_context()

_HTTP_TIMEOUT_S = 10.0


class HostContext:
    """一次沙箱调用的宿主侧上下文（内存 FS + allowlist + 限制 + 网络事件）"""

    def __init__(self, request: SandboxRequest):
        self.request = request
        self.limits: SandboxLimits = request.limits
        self.allowlist = request.allowlist
        self.net_events: list[NetEvent] = []
        self.files: Dict[str, str] = {}
        for f in request.files:
            name = f.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            self.files[name] = f.content_b64
        # M1：配额为单请求内计数（真实每日配额由 API 侧 Redis 账本在 M4 BudgetLedger 统一实现）
        self._fetch_count = 0

    # ==================== JS/Wasmtime 路径的宿主函数（同步回调） ====================

    def http_fetch(self, url: str, body: Optional[str], content_type: Optional[str]) -> Tuple[int, str, str]:
        """
        WIT host.http-fetch 的 Python 实现。
        返回 (ok, payload) 形态无法直接表达，这里返回 (status, body, error)：
        error 非空表示拒绝/失败，由引擎侧映射为 result.err。
        """
        event = NetEvent(url=url, allowed=False)
        try:
            return self._do_fetch(url, body, content_type, event)
        finally:
            self.net_events.append(event)

    def _do_fetch(self, url: str, body: Optional[str], content_type: Optional[str], event: NetEvent) -> Tuple[int, str, str]:
        # 1. URL 级检查（scheme/端口/IP 字面量/私网/allowlist）
        allowed, reason = check_url(url, self.allowlist)
        if not allowed:
            event.reason = reason
            return 0, "", f"denied: {reason}"

        # 2. DNS 解析后的 SSRF 二次检查（防 allowlist 域名 rebinding 到私网）
        import urllib.parse as _up

        hostname = _up.urlsplit(url).hostname or ""
        allowed, reason = resolve_and_check(hostname, self.allowlist)
        if not allowed:
            event.reason = reason
            return 0, "", f"denied: {reason}"

        # 3. 配额与尺寸
        self._fetch_count += 1
        if self._fetch_count > self.limits.http_fetch_daily_quota:
            event.reason = "quota"
            return 0, "", "denied: quota exceeded"

        max_body = self.limits.http_fetch_max_body_mb * 1024 * 1024
        body_bytes = body.encode("utf-8") if body else None
        if body_bytes and len(body_bytes) > max_body:
            event.reason = "body_too_large"
            return 0, "", "denied: request body too large"

        # 4. 执行请求（尺寸上限内读取响应，超出截断）
        try:
            req = urllib.request.Request(url, data=body_bytes, method="POST" if body_bytes else "GET")
            if content_type:
                req.add_header("Content-Type", content_type)
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S, context=_SSL_CONTEXT) as resp:
                event.allowed = True
                event.status = resp.status
                chunks = []
                total = 0
                truncated = False
                while True:
                    chunk = resp.read(io.DEFAULT_BUFFER_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_body:
                        chunks.append(chunk[: max_body - (total - len(chunk))])
                        truncated = True
                        break
                    chunks.append(chunk)
                payload = b"".join(chunks).decode("utf-8", errors="replace")
                if truncated:
                    payload += "\n[folio-sandbox: response truncated]"
                event.bytes_in = total
                event.bytes_out = len(body_bytes or b"")
                return resp.status, payload, ""
        except urllib.error.HTTPError as e:
            # HTTP 4xx/5xx 是"成功的网络访问"，状态码如实返回给用户代码
            event.allowed = True
            event.status = e.code
            event.bytes_in = 0
            return e.code, "", ""
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            event.reason = "network_error"
            return 0, "", f"network error: {e}"

    def read_file(self, name: str) -> str:
        """内存 FS 读取：返回 base64 内容；文件名非法或缺失时抛 KeyError（引擎映射为错误）"""
        clean = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if clean != name or clean not in self.files:
            raise KeyError(f"file not found: {name}")
        return self.files[clean]

    def list_files(self) -> list:
        return list(self.files.keys())


def decode_b64(b64: str) -> bytes:
    """内存 FS 内容解码（引擎侧使用）"""
    return base64.b64decode(b64)
