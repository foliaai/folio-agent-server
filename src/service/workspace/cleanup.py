#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : cleanup.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    工作空间物理清理（设计 §3.4，D9/D11/D21）：进程内 APScheduler 每日一次。

    分级：
    - 临时区：24h 未绑定会话 → 对象+行全清（D11）
    - 解析失败行：> 7 天 → 清（D21，不自动重试，行过期即收）
    - 软删会话 upload 附件：软删 > 30 天 → 硬删
    - 软删会话 agent 产物：软删 > 7 天 → 硬删（派生物，比 upload 激进）
    单批 ≤ cleanup_batch_size 个对象（防 IO 突刺）；对象删完再删行
    （delete-if-exists 语义，悬空行自愈）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
from datetime import datetime, timedelta

from db.storage.models import build_file_prefix
from loguru import logger

from src.utils.config_manager import get_config_manager


def _cfg() -> dict:
    cm = get_config_manager()
    return {
        "hour": int(cm.get("workspace.cleanup_hour", 3)),
        "upload_days": int(cm.get("workspace.upload_retention_days", 30)),
        "agent_days": int(cm.get("workspace.agent_retention_days", 7)),
        "temp_hours": int(cm.get("workspace.temp_retention_hours", 24)),
        "failed_days": int(cm.get("workspace.failed_parse_retention_days", 7)),
        "batch": int(cm.get("workspace.cleanup_batch_size", 500)),
    }


async def run_workspace_cleanup() -> dict:
    """执行一轮清理（幂等；供调度器与手工触发共用）"""
    from db.mysql.repositories import agent_session_file_repo as file_repo
    from db.storage.repositories import delete_prefix

    cfg = _cfg()
    now = datetime.now()
    budget = cfg["batch"]
    summary = {"temp": 0, "failed": 0, "upload": 0, "agent": 0, "errors": 0}

    async def _purge(infos: list, counter_key: str) -> None:
        nonlocal budget
        for info in infos:
            if budget <= 0:
                return
            obj_session = info["session_id"] or f"temp-{info['file_id']}"
            prefix = build_file_prefix(info["user_id"], obj_session, info["file_id"])
            try:
                await delete_prefix(prefix)
                await asyncio.to_thread(file_repo.hard_delete_rows, [info["file_id"]])
                summary[counter_key] += 1
                budget -= 1
            except Exception as e:  # noqa: BLE001 —— 单文件失败不拖垮整轮
                summary["errors"] += 1
                logger.warning(f"[cleanup] 清理失败 {info['file_id']}: {e}")

    # 1) 临时区孤儿（D11）
    try:
        expired_temp = await asyncio.to_thread(
            file_repo.list_expired_temp,
            before=now - timedelta(hours=cfg["temp_hours"]),
            limit=budget,
        )
        await _purge(expired_temp, "temp")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[cleanup] 临时区扫描失败: {e}")

    # 2) 解析失败行（D21）
    try:
        expired_failed = await asyncio.to_thread(
            file_repo.list_expired_failed,
            before=now - timedelta(days=cfg["failed_days"]),
            limit=budget,
        )
        await _purge(expired_failed, "failed")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[cleanup] 失败行扫描失败: {e}")

    # 3) 软删会话附件分级（D9：upload 30 天 / agent 7 天）
    for origin, days, key in (
        ("agent", cfg["agent_days"], "agent"),
        ("upload", cfg["upload_days"], "upload"),
    ):
        if budget <= 0:
            break
        try:
            rows = await asyncio.to_thread(
                file_repo.list_files_of_softdeleted_sessions,
                deleted_before=now - timedelta(days=days),
                origin=origin,
                limit=budget,
            )
            await _purge(rows, key)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[cleanup] 软删会话扫描失败 origin={origin}: {e}")

    logger.info(f"[cleanup] 完成: {summary}")
    return summary


def start_cleanup_scheduler() -> "AsyncIOScheduler | None":
    """挂在 FastAPI lifespan：每日 cfg.hour 点跑一轮（单实例模式，无并发重入）"""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[cleanup] APScheduler 不可用，跳过清理任务: {e}")
        return None

    cfg = _cfg()
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_workspace_cleanup,
        trigger=CronTrigger(hour=cfg["hour"], minute=0),
        id="workspace-cleanup",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"[cleanup] 工作空间清理任务已排: 每日 {cfg['hour']:02d}:00")
    return scheduler
