#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : v1_1_0_agent_message_to_mongodb.py
@Author  : caixiongjiang
@Date    : 2026/10/04
@Function:
    v1.1.0 升级脚本：消息正文存储 MySQL → MongoDB（与 AKS 双库分工对齐）。

    1. 读 MySQL agent_message 全量行 → 按行原样写入 MongoDB agent_message
       集合（message_id 保留，_id 直接沿用，回放顺序不变）
    2. 校验写入条数一致后 DROP TABLE agent_message（幂等：表不存在即跳过；
       Mongo 已有数据时跳过迁移——脚本可重复执行）

    使用：
        python scripts/mysql/migrations/v1_1_0_agent_message_to_mongodb.py

@Modify History:
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text


async def migrate() -> None:
    from db.mongodb import get_mongodb_manager
    from db.mongodb.models.agent_message import AgentMessage
    from db.mysql.connection import get_mysql_manager

    # Mongo 侧须先完成 Beanie 初始化
    await get_mongodb_manager()

    mysql = get_mysql_manager()
    mysql.init_db()

    with mysql.engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_message'"
            )
        ).scalar()
        if not exists:
            logger.info("⏭️  MySQL agent_message 表不存在，无需迁移")
            return

        rows = conn.execute(
            text(
                "SELECT message_id, session_id, user_id, role, content, thinking, "
                "thinking_ms, tool_calls, `usage`, finish_reason, items, "
                "creator, updater, deleted, status, create_time, update_time "
                "FROM agent_message"
            )
        ).mappings().all()

    existing = await AgentMessage.find().count()
    if existing > 0:
        logger.warning(
            f"Mongo agent_message 已有 {existing} 条数据，默认跳过迁移（--force 重导）"
        )
        return

    if not rows:
        logger.info("MySQL agent_message 为空表，直接 DROP")
    else:
        import json

        def _json(col):
            if col is None:
                return None
            if isinstance(col, (dict, list)):
                return col
            return json.loads(col)

        docs = [
            AgentMessage(
                _id=r["message_id"],
                session_id=r["session_id"],
                user_id=r["user_id"],
                role=r["role"],
                content=r["content"] or "",
                thinking=r["thinking"],
                thinking_ms=r["thinking_ms"],
                tool_calls=_json(r["tool_calls"]) or [],
                usage=_json(r["usage"]),
                finish_reason=r["finish_reason"],
                items=_json(r["items"]) or [],
                status=r["status"] or 0,
                creator=r["creator"] or "",
                updater=r["updater"] or "",
                deleted=r["deleted"] or 0,
                create_time=r["create_time"],
                update_time=r["update_time"],
            )
            for r in rows
        ]
        await AgentMessage.insert_many(docs)
        migrated = await AgentMessage.find().count()
        logger.info(f"✅ 迁移 {len(docs)} 条 → Mongo 实际 {migrated} 条")
        if migrated != len(docs):
            logger.error("❌ 条数不一致，保留 MySQL 表以便排查（不 DROP）")
            return

    with mysql.engine.begin() as conn:
        conn.execute(text("DROP TABLE agent_message"))
    logger.info("🗑️  已 DROP MySQL agent_message 表")


if __name__ == "__main__":
    asyncio.run(migrate())
