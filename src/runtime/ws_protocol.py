#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : ws_protocol.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    平台 WS 帧协议（架构方案 §8.3）。
    AKS 现有帧协议升格为平台协议：agent_apps 与 AKS 各自实现、帧名与载荷
    字段保持一致（对照档 docs/ws-protocol.md，M2 随本文件落档）。
    agent_apps 新增：subagent.*（M4 扇出透出）、budget.*（M4 护栏透出）、
    usage 帧。retrieval.* 为 AKS 专属，本服务不发。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ServerFrameType(str, enum.Enum):
    """S→C 帧（帧名与载荷字段对齐 AKS ChatEventType）"""

    SESSION_READY = "session.ready"
    THINKING_DELTA = "thinking.delta"        # {"text"}
    CONTENT_DELTA = "content.delta"          # {"text"}
    TOOL_CALL_STARTED = "tool_call.started"  # {"index"?, "id", "name"}
    TOOL_CALL_ARGS_DELTA = "tool_call.args_delta"  # {"index"?, "id", "text"}
    TOOL_CALL_COMPLETED = "tool_call.completed"    # {"id", "name", "args", "result_brief", "time_ms"}
    MESSAGE_DONE = "message.done"            # {"message_id"?, "role", "finish_reason", "usage"?, "time_ms"}
    TURN_DONE = "turn.done"                  # {"rounds", "tool_calls_count", "time_ms"}
    USAGE = "usage"                          # {"input_tokens", "output_tokens", "total_tokens", "cached_tokens"}
    ERROR = "error"                          # {"phase", "error", "cancelled"?}
    PONG = "pong"                            # {}

    # agent_apps 新增（平台协议扩展；M4 起实际使用）
    SUBAGENT_STARTED = "subagent.started"    # {"name", "task"}
    SUBAGENT_DONE = "subagent.done"          # {"name", "time_ms", "tokens"?}
    BUDGET_WARNING = "budget.warning"        # {"used_ratio", "remaining"}
    BUDGET_EXCEEDED = "budget.exceeded"      # {"reason"}


class ClientFrameType(str, enum.Enum):
    """C→S 帧（对齐 AKS ws 路由的入站帧）"""

    START = "start"   # {message, model?, session_id?}
    STOP = "stop"
    PING = "ping"


@dataclass
class ServerFrame:
    """S→C 帧的可序列化形态"""

    type: ServerFrameType
    data: Dict[str, Any] = field(default_factory=dict)

    def encode(self) -> dict:
        return {"type": self.type.value, "data": self.data}


def make_frame(ftype: ServerFrameType, **data: Any) -> dict:
    """构造一条服务端帧的 wire dict（json.dumps 即发）"""
    return ServerFrame(ftype, data).encode()


def make_error_frame(phase: str, error: str, extra: Optional[Dict[str, Any]] = None) -> dict:
    data = {"phase": phase, "error": error}
    if extra:
        data.update(extra)
    return make_frame(ServerFrameType.ERROR, **data)


def parse_client_frame(raw: str) -> Optional[dict]:
    """
    解析 C→S 帧。

    Returns:
        {"type": ClientFrameType, "data": {...}}；坏帧/未知类型返回 None
    """
    import json

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        ftype = ClientFrameType(obj.get("type", ""))
    except ValueError:
        return None
    data = obj.get("data")
    return {"type": ftype, "data": data if isinstance(data, dict) else {}}
