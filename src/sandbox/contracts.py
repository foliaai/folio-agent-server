#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : contracts.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    沙箱 API↔sidecar 协议契约（双端共享）。
    传输：stdin/stdout 上的行分隔 JSON（line-delimited JSON）。
    帧类型：ready / ping / pong / exec / result。
    本模块不得依赖 sidecar 或 API 侧任何业务模块，保持双端可导入。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError


class SandboxLang(str, Enum):
    """沙箱支持的语言（按引擎分发）"""

    JS = "js"            # JS/TS 编译为 wasm32-wasi（wasmtime_engine）
    PYTHON = "python"    # Pyodide（nodejs/runner.js 孙进程）


class SandboxErrorCode(str, Enum):
    """沙箱执行错误码（verdict 同源）"""

    FUEL_EXHAUSTED = "fuel_exhausted"        # 指令配额耗尽（while True 等）
    OOM = "oom"                              # 内存配额超限（Store limiter / Node heap）
    TIMEOUT = "timeout"                      # 墙钟超时
    DENIED_FS = "denied_fs"                  # 越权文件系统访问
    DENIED_NET = "denied_net"                # 越权网络访问（allowlist 外 / 私网 / 非 HTTPS）
    OUTPUT_TRUNCATED = "output_truncated"    # stdout/返回值超上限被截断（执行本身成功）
    QUOTA_EXCEEDED = "quota_exceeded"        # http_fetch 每日配额耗尽
    ENGINE_CRASH = "engine_crash"            # 引擎崩溃（sidecar 侧已捕获，进程可能已自杀重启）
    BAD_CODE = "bad_code"                    # 代码无法编译/导入（wasm 校验失败、Python 语法错误）
    INTERNAL = "internal"                    # 其他内部错误


class SandboxFile(BaseModel):
    """内存 FS 输入文件（base64 编码内容，调用结束即丢弃，防跨调用状态泄漏）"""

    name: str = Field(..., description="文件名（basename，禁止路径分隔符）")
    content_b64: str = Field(..., description="文件内容 base64")


class SandboxLimits(BaseModel):
    """单次调用的资源限制（API 侧已钳制，sidecar 直接执行不再放宽）"""

    fuel_per_call: int = 1_000_000_000
    wall_clock_timeout_ms: int = 10_000
    memory_limit_mb: int = 64
    output_limit_mb: int = 1
    http_fetch_max_body_mb: int = 5
    http_fetch_daily_quota: int = 1000
    node_runner_timeout_ms: int = 15_000


class SandboxRequest(BaseModel):
    """API→sidecar 执行请求（帧 type=exec）"""

    id: str
    lang: SandboxLang
    code: str = Field(..., description="待执行源码（js 为已编译 wasm component 的 base64，python 为源码）")
    input: str = Field(default="", description="传给工具入口的标准输入/run(input) 参数")
    files: List[SandboxFile] = Field(default_factory=list, description="内存 FS 输入文件")
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
    allowlist: List[str] = Field(default_factory=list, description="http_fetch 域名 allowlist（空=全拒）")
    meta: Dict[str, str] = Field(default_factory=dict, description="审计元数据（agent_id/user_id/tool 等透传）")


class NetEvent(BaseModel):
    """一次 http_fetch 宿主函数调用的记录（sidecar 回传，API 侧落审计）"""

    url: str
    allowed: bool
    status: Optional[int] = None
    bytes_in: int = 0
    bytes_out: int = 0
    reason: Optional[str] = None     # 拒绝原因（denied/quota/private_host/...）


class SandboxResult(BaseModel):
    """单次执行的产物（result 帧/引擎结果共用结构）"""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    result: Optional[str] = None        # JS 引擎导出函数返回值（JSON 字符串）
    error_code: Optional[SandboxErrorCode] = None
    error_detail: str = ""
    fuel_used: int = 0
    wall_ms: int = 0
    net_events: List[NetEvent] = Field(default_factory=list)


class SandboxResponse(BaseModel):
    """sidecar→API 响应（帧 type=result）"""

    type: str = Field(default="result", description="协议帧类型标记")
    id: str
    ok: bool
    result: SandboxResult


# ==================== 控制帧 ====================


class ReadyFrame(BaseModel):
    """sidecar 启动完成宣告（进程第一条输出）"""

    type: str = "ready"
    engines: List[str]


class PingFrame(BaseModel):
    type: str = "ping"


class PongFrame(BaseModel):
    type: str = "pong"


def encode_frame(model: BaseModel) -> str:
    """序列化为一行 JSON（行分隔协议的写侧）"""
    return model.model_dump_json(exclude_none=False)


def decode_exec_frame(line: str) -> Optional[SandboxRequest]:
    """解析 exec 帧；非 exec 帧或缺必填字段的坏帧返回 None"""
    import json

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "exec":
        return None
    data.pop("type", None)
    try:
        return SandboxRequest.model_validate(data)
    except ValidationError:
        return None


def decode_line(line: str) -> Optional[Dict[str, Any]]:
    """通用行解析（控制帧分发用）"""
    import json

    try:
        data = json.loads(line)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None
