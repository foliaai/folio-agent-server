#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : workspace_preload.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    沙箱预载（设计 §3.3 读挂载，D8）：code_execute 调用开始时把会话工作空间
    文件预载进本次调用的内存 FS（SandboxRequest.files 通道，调用结束即丢）。

    配额（config [sandbox.workspace]，首版建议值）：单次文件数 / 总量上限；
    超限文件跳过并在工具结果里说明。范围=kind=file（含 .md 解析产物，
    不含图片——代码分析用不上图片二进制）。
    只读挂载与写回（drain/files_out）是二期：本期预载文件在沙箱内可写
    （写不回工作空间，调用结束即丢），不破坏 ADR-0001 零宿主 FS。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
from dataclasses import dataclass, field
from typing import List

from loguru import logger

from db.storage.models import build_raw_key
from db.storage.repositories import get_object_any
from src.sandbox.contracts import SandboxFile
from src.tools.registry import ToolDeps
from src.utils.config_manager import get_config_manager


@dataclass
class PreloadResult:
    files: List[SandboxFile] = field(default_factory=list)
    skipped_note: str = ""


async def build_preload_files(deps: ToolDeps) -> PreloadResult:
    """会话工作空间 → 沙箱内存 FS 输入文件（无会话/无文件/超限都优雅降级）"""
    if not deps.session_id:
        return PreloadResult()
    cm = get_config_manager()
    max_files = int(cm.get("sandbox.workspace.preload_max_files", 20))
    max_total_mb = int(cm.get("sandbox.workspace.preload_max_total_mb", 32))
    max_total_bytes = max_total_mb * 1024 * 1024

    from db.mysql.repositories import agent_session_file_repo as file_repo

    try:
        items = await asyncio.to_thread(file_repo.list_by_session, deps.session_id)
    except Exception as e:  # noqa: BLE001 —— 清单失败不阻断代码执行
        logger.warning(f"[workspace] 预载清单失败（跳过预载）: {e}")
        return PreloadResult()

    files: List[SandboxFile] = []
    skipped: List[str] = []
    total = 0
    for info in items:
        if info.get("kind") != "file":
            continue  # 图片不预载
        if info.get("parse_status") != "ready":
            continue
        if len(files) >= max_files:
            skipped.append(f"{info['name']}(超出单次 {max_files} 个文件上限)")
            continue
        size = int(info.get("size") or 0)
        if total + size > max_total_bytes:
            skipped.append(f"{info['name']}(超出预载总量 {max_total_mb}MB)")
            continue
        obj_session = info["session_id"] or f"temp-{info['file_id']}"
        keys = [build_raw_key(info["user_id"], obj_session, info["file_id"])]
        if info["session_id"]:
            keys.append(build_raw_key(info["user_id"], f"temp-{info['file_id']}", info["file_id"]))
        try:
            data = await get_object_any(keys)
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{info['name']}(读取失败)")
            logger.warning(f"[workspace] 预载读取失败: {info['file_id']}, {e}")
            continue
        import base64

        files.append(SandboxFile(name=info["name"], content_b64=base64.b64encode(data).decode("ascii")))
        total += len(data)

    note = ""
    if skipped:
        note = "部分工作空间文件未预载: " + "、".join(skipped[:5])
    return PreloadResult(files=files, skipped_note=note)
