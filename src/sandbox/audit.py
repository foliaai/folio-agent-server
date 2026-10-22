#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : audit.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    沙箱审计日志（API 侧）：消费 SandboxResponse 组装记录，落 JSON 行文件。
    sidecar 本身无文件写权限——网络访问等事件经响应帧回传，统一在此落盘。
    记录结构对齐 ADR-0001：{ts, agent_id, tool, lang, fuel_used, wall_ms, urls[], verdict}。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.sandbox.contracts import SandboxLang, SandboxRequest, SandboxResult


class SandboxAuditLogger:
    """审计 JSON 行落盘器（进程内单例使用，无锁——单 asyncio 循环内调用）"""

    def __init__(self, audit_dir: str = "logs/sandbox"):
        self._audit_dir = Path(audit_dir)
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._sink_name = "sandbox"

    def _write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        day_file = self._audit_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(day_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            # 审计落盘失败不能影响业务链路，但要留下日志痕迹
            logger.bind(name=self._sink_name).error(f"审计写入失败: {e} | record={line[:500]}")

    def log_execution(
        self,
        request: SandboxRequest,
        result: SandboxResult,
    ) -> None:
        """一次沙箱调用的完整审计记录"""
        verdict = "ok" if result.ok else (result.error_code.value if result.error_code else "unknown")

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "agent_id": request.meta.get("agent_id", ""),
            "user_id": request.meta.get("user_id", ""),
            "tool": request.meta.get("tool", ""),
            "request_id": request.id,
            "lang": request.lang.value,
            "fuel_used": result.fuel_used,
            "wall_ms": result.wall_ms,
            "verdict": verdict,
            "error_detail": result.error_detail or "",
            "urls": [
                {
                    "url": ev.url,
                    "allowed": ev.allowed,
                    "status": ev.status,
                    "bytes_in": ev.bytes_in,
                    "bytes_out": ev.bytes_out,
                    "reason": ev.reason,
                }
                for ev in result.net_events
            ],
        }
        self._write(record)

    def log_sidecar_event(self, event: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """sidecar 进程生命周期事件（重启/自杀/池扩缩）"""
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "detail": detail or {},
        }
        self._write(record)


def summarize_verdicts(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """审计记录的 verdict 计数（健康检查/运营看板用）"""
    counts: Dict[str, int] = {}
    for r in records:
        v = r.get("verdict", "unknown")
        counts[v] = counts.get(v, 0) + 1
    return counts
