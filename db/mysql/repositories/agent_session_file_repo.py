#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : agent_session_file_repo.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    会话工作空间文件目录 repository（MySQL agent_session_file）

    行粒度：一个工作空间文件一行（上传附件 / agent 执行产物）。
    物理内容在 MinIO（db/storage，connection/models/repositories 三层），
    本 repo 只管目录：
    CRUD / 解析状态推进 / 临时区绑定（D1）/ 配额统计 / 清理扫描（D9、D11）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger
from sqlalchemy import delete, func, select, update

from db.mysql.connection import get_mysql_manager
from db.mysql.models.agent_session_file import AgentSessionFile


def _file_id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now()


def _to_info(f: AgentSessionFile) -> Dict[str, Any]:
    return {
        "file_id": f.id,
        "session_id": f.session_id,
        "user_id": f.user_id,
        "kind": f.kind,
        "origin": f.origin,
        "name": f.name,
        "mime": f.mime,
        "size": f.size,
        "parse_status": f.parse_status,
        "parse_error": f.parse_error,
        "md_object_key": f.md_object_key,
        "md_chars": f.md_chars,
        "asset_count": f.asset_count,
        "create_time": f.create_time.isoformat() if f.create_time else None,
    }


def create_file(
    *,
    user_id: str,
    kind: str,
    origin: str,
    name: str,
    mime: str,
    size: int,
    session_id: Optional[str] = None,
    parse_status: str = "pending",
) -> Dict[str, Any]:
    """新建目录行（先行后对象防孤儿，D9 写序：行先落，对象随后写）"""
    manager = get_mysql_manager()
    row = AgentSessionFile(
        id=_file_id(),
        user_id=user_id,
        session_id=session_id,
        kind=kind,
        origin=origin,
        name=name[:255],
        mime=mime[:128],
        size=size,
        parse_status=parse_status,
        creator=user_id,
        updater=user_id,
    )
    with manager.SessionLocal() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_info(row)


def get_file(file_id: str) -> Optional[Dict[str, Any]]:
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSessionFile, file_id)
        if row is None or row.deleted:
            return None
        return _to_info(row)


def list_by_session(
    session_id: str, *, origin: Optional[str] = None, limit: int = 200
) -> List[Dict[str, Any]]:
    """会话工作空间文件列表（附件轮询 / 产物区数据源）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        stmt = (
            select(AgentSessionFile)
            .where(
                AgentSessionFile.session_id == session_id,
                AgentSessionFile.deleted == 0,
            )
            .order_by(AgentSessionFile.create_time.asc())
            .limit(limit)
        )
        if origin:
            stmt = stmt.where(AgentSessionFile.origin == origin)
        rows = session.execute(stmt).scalars().all()
        return [_to_info(r) for r in rows]


def count_session_files(session_id: str) -> int:
    """会话附件计数（D10：单会话 ≤ 20）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AgentSessionFile)
                .where(
                    AgentSessionFile.session_id == session_id,
                    AgentSessionFile.deleted == 0,
                )
            )
            or 0
        )


def sum_session_bytes(session_id: str, *, origin: Optional[str] = None) -> int:
    """会话工作空间占用字节数（配额校验；origin 限定时算子配额）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        stmt = select(func.coalesce(func.sum(AgentSessionFile.size), 0)).where(
            AgentSessionFile.session_id == session_id,
            AgentSessionFile.deleted == 0,
        )
        if origin:
            stmt = stmt.where(AgentSessionFile.origin == origin)
        return int(session.scalar(stmt) or 0)


def update_parse_status(
    *,
    file_id: str,
    parse_status: str,
    parse_error: Optional[str] = None,
    md_object_key: Optional[str] = None,
    md_chars: Optional[int] = None,
    asset_count: Optional[int] = None,
) -> bool:
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSessionFile, file_id)
        if row is None:
            return False
        row.parse_status = parse_status
        if parse_error is not None:
            row.parse_error = parse_error[:2000]
        if md_object_key is not None:
            row.md_object_key = md_object_key
        if md_chars is not None:
            row.md_chars = md_chars
        if asset_count is not None:
            row.asset_count = asset_count
        row.update_time = _now()
        session.commit()
        return True


def bind_temp_files(*, file_ids: Sequence[str], user_id: str, session_id: str) -> int:
    """临时区文件绑定到会话（D1：start 帧携带时搬运归位；只认领本用户的临时行）"""
    if not file_ids:
        return 0
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        result = session.execute(
            update(AgentSessionFile)
            .where(
                AgentSessionFile.id.in_(list(file_ids)),
                AgentSessionFile.user_id == user_id,
                AgentSessionFile.session_id.is_(None),
                AgentSessionFile.deleted == 0,
            )
            .values(session_id=session_id, update_time=_now())
        )
        session.commit()
        return int(result.rowcount or 0)


def soft_delete_file(*, file_id: str, user_id: str) -> bool:
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        row = session.get(AgentSessionFile, file_id)
        if row is None or row.deleted or row.user_id != user_id:
            return False
        row.deleted = 1
        row.updater = user_id
        row.update_time = _now()
        session.commit()
        return True


# ==================== 清理扫描（D9 / D11，cleanup 任务消费） ====================


def list_expired_temp(*, before: datetime, limit: int) -> List[Dict[str, Any]]:
    """临时区超时行（session_id IS NULL 且 create_time < before）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        rows = (
            session.execute(
                select(AgentSessionFile)
                .where(
                    AgentSessionFile.session_id.is_(None),
                    AgentSessionFile.deleted == 0,
                    AgentSessionFile.create_time < before,
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_to_info(r) for r in rows]


def list_expired_failed(*, before: datetime, limit: int) -> List[Dict[str, Any]]:
    """解析失败超时行（failed 且 update_time < before；对象本身很小，清行保列表干净）"""
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        rows = (
            session.execute(
                select(AgentSessionFile)
                .where(
                    AgentSessionFile.parse_status == "failed",
                    AgentSessionFile.deleted == 0,
                    AgentSessionFile.update_time < before,
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_to_info(r) for r in rows]


def list_files_of_softdeleted_sessions(
    *, deleted_before: datetime, origin: str, limit: int
) -> List[Dict[str, Any]]:
    """
    软删会话的文件扫描（join agent_session：deleted=1 且 update_time < deleted_before）。
    origin 分级：upload 30 天 / agent 7 天（D9）——调用方分两次传参。
    """
    from db.mysql.models.agent_conversation import AgentSession

    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        rows = (
            session.execute(
                select(AgentSessionFile)
                .join(
                    AgentSession,
                    AgentSession.session_id == AgentSessionFile.session_id,
                )
                .where(
                    AgentSession.deleted == 1,
                    AgentSession.update_time < deleted_before,
                    AgentSessionFile.deleted == 0,
                    AgentSessionFile.origin == origin,
                )
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_to_info(r) for r in rows]


def hard_delete_rows(file_ids: Sequence[str]) -> int:
    """物理删除目录行（清理任务的收尾；对象先删、行后删，delete-if-exists 语义）"""
    if not file_ids:
        return 0
    manager = get_mysql_manager()
    with manager.SessionLocal() as session:
        result = session.execute(
            delete(AgentSessionFile).where(AgentSessionFile.id.in_(list(file_ids)))
        )
        session.commit()
        return int(result.rowcount or 0)


def purge_file_row(file_id: str) -> None:
    """单行硬删（上传接口 5xx 回滚用）"""
    hard_delete_rows([file_id])


# ==================== agent 产物登记（drain 写回用，二期 files_out 落地时消费） ====================


def create_agent_output(
    *, user_id: str, session_id: str, name: str, mime: str, size: int
) -> Dict[str, Any]:
    """登记一个 agent 执行产物（origin=agent；配额校验在调用方 service 层）"""
    return create_file(
        user_id=user_id,
        session_id=session_id,
        kind="file",
        origin="agent",
        name=name,
        mime=mime,
        size=size,
        parse_status="ready",
    )
