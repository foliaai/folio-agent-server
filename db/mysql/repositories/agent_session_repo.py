#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : agent_session_repo.py
@Author  : caixiongjiang
@Date    : 2026/10/04
@Function:
    通用 Agent 会话元信息 repository（MySQL agent_session）

    会话元信息在 MySQL（本文件，对齐 AKS ChatSession 表）；消息正文在
    MongoDB（db/mongodb/repositories/agent_message_repo.py，对齐 AKS
    chat_message_repo）——双库分工与 AKS 一致。

    消息的写入/查询/清空走 Mongo repo；本文件只管会话行 CRUD 与
    落库后的计数/活跃时间推进（touch_turn）。
@Modify History:
    2026-10-04 消息侧迁 MongoDB，本 repo 收敛为 session-only

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import func, select

from db.mysql.connection import get_mysql_manager
from db.mysql.models.agent_conversation import AgentSession

# 首条用户消息生成标题的截断长度（AKS 同款：LLM 起标题是后续增强）
TITLE_MAX_LEN = 30


def _session_id() -> str:
    return uuid.uuid4().hex[:16]


def _title_from_message(message: str) -> str:
    text = " ".join(message.split())
    return text[:TITLE_MAX_LEN] + ("…" if len(text) > TITLE_MAX_LEN else "") or "新会话"


def _now() -> datetime:
    return datetime.now()


def _to_session_info(s: AgentSession) -> Dict[str, Any]:
    return {
        "session_id": s.session_id,
        "user_id": s.user_id,
        "title": s.title,
        "model": s.model,
        "thinking_level": s.thinking_level,
        "message_count": s.message_count,
        "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
        "create_time": s.create_time.isoformat() if s.create_time else None,
    }


def create_session(
    *,
    user_id: str,
    title: str = "新会话",
    model: Optional[str] = None,
    thinking_level: str = "off",
) -> Dict[str, Any]:
    """创建会话（REST / WS 首条消息时调用）"""
    manager = get_mysql_manager()
    row = AgentSession(
        session_id=_session_id(),
        user_id=user_id,
        title=title or "新会话",
        model=model,
        thinking_level=thinking_level or "off",
        creator=user_id,
        updater=user_id,
    )
    with manager.SessionLocal() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_session_info(row)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSession, session_id)
        if row is None or row.deleted:
            return None
        return _to_session_info(row)


def list_sessions(
    *,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    """会话列表（活跃度倒序 + 软删过滤）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        base = select(AgentSession).where(
            AgentSession.user_id == user_id, AgentSession.deleted == 0
        )
        total = session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        rows = (
            session.execute(
                base.order_by(
                    # MySQL 的 DESC 天然把 NULL 排在最后（新会话未发言不置顶）
                    AgentSession.last_message_at.desc(),
                    AgentSession.create_time.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return [_to_session_info(r) for r in rows], int(total)


def rename_session(*, session_id: str, title: str, user_id: str) -> Optional[Dict[str, Any]]:
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSession, session_id)
        if row is None or row.deleted or row.user_id != user_id:
            return None
        row.title = title.strip()[:255] or row.title
        row.updater = user_id
        row.update_time = _now()
        session.commit()
        session.refresh(row)
        return _to_session_info(row)


def soft_delete_session(*, session_id: str, user_id: str) -> bool:
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSession, session_id)
        if row is None or row.deleted or row.user_id != user_id:
            return False
        row.deleted = 1
        row.updater = user_id
        row.update_time = _now()
        session.commit()
        return True


def touch_turn(
    *,
    session_id: str,
    user_id: str,
    first_message: str,
    added_count: int,
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> None:
    """
    落库后推进会话行（对齐 AKS touch_session + 消息计数）：
    行不存在时 upsert（标题取首条用户消息截断），计数累加、活跃时间刷新。
    """
    manager = get_mysql_manager()
    now = _now()
    with manager.SessionLocal() as session:
        row = session.get(AgentSession, session_id)
        if row is None:
            # 前端直连旧会话 / REST 未建：兜底 upsert
            row = AgentSession(
                session_id=session_id,
                user_id=user_id,
                title=_title_from_message(first_message),
                creator=user_id,
                updater=user_id,
            )
            session.add(row)
        row.message_count = (row.message_count or 0) + added_count
        row.last_message_at = now
        if model:
            row.model = model
        if thinking_level:
            row.thinking_level = thinking_level
        row.updater = user_id
        row.update_time = now
        session.commit()


def reset_message_count(*, session_id: str, user_id: str) -> bool:
    """清空消息后重置计数（配合 Mongo 软删；对齐 AKS reset_message_count）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSession, session_id)
        if row is None or row.deleted or row.user_id != user_id:
            return False
        row.message_count = 0
        row.updater = user_id
        row.update_time = _now()
        session.commit()
        return True
