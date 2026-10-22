#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : sessions.py（api/routes/v1）
@Author  : caixiongjiang
@Date    : 2026/10/04
@Function:
    通用 Agent 会话 REST 路由（前端历史会话侧栏数据源，接口形状与 AKS 的
    /api/chat/sessions 对齐——分页字段 / 软删语义 / 回放正序分页）。

    POST   /api/v1/sessions                       建会话
    GET    /api/v1/sessions?page&page_size        会话列表（活跃度倒序）
    GET    /api/v1/sessions/{sid}                 会话详情
    PATCH  /api/v1/sessions/{sid}                 重命名
    DELETE /api/v1/sessions/{sid}                 软删
    GET    /api/v1/sessions/{sid}/messages        消息回放（正序分页）
    DELETE /api/v1/sessions/{sid}/messages        清空消息（会话保留）

    repository 为同步 SQLAlchemy，路由层统一 asyncio.to_thread 包装。
@Modify History:
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from api.dependencies.auth import get_current_user_id
from db.mongodb.repositories import agent_message_repo
from db.mysql.repositories import agent_session_repo

router = APIRouter(tags=["Sessions"])


def _reasoning_text(item: dict) -> str:
    """reasoning item 正文：summary/content 列表里的 text part 拼接（缺形状返回空）"""
    parts: list[str] = []
    for key in ("summary", "content"):
        v = item.get(key)
        if isinstance(v, str):
            if v:
                parts.append(v)
        elif isinstance(v, list):
            for p in v:
                if isinstance(p, dict) and p.get("text"):
                    parts.append(str(p["text"]))
    return "".join(parts)


def _build_segments(m) -> list | None:
    """
    assistant 行：从 items（SDK 原生 input items，按发生顺序）派生思考/工具
    交错段，供前端按调用顺序分段渲染思考（而非整轮思考固定在首行）。
    - reasoning item → {"kind": "thinking", "text": …}
    - function_call item → {"kind": "tool", "id": call_id, "name": …}
      （id 与 tool_calls[].id 同为 call_id，前端据此取参数/结果/耗时）
    无 items 或派生为空（纯文本轮）→ None，前端按无轨迹渲染。
    """
    if m.role != "assistant":
        return None
    segs: list[dict] = []
    for it in m.items or []:
        if not isinstance(it, dict):
            continue
        it_type = it.get("type")
        if it_type == "reasoning":
            text = _reasoning_text(it)
            if text:
                segs.append({"kind": "thinking", "text": text})
        elif it_type == "function_call":
            call_id = it.get("call_id") or it.get("id") or ""
            if call_id:
                segs.append({"kind": "tool", "id": call_id, "name": it.get("name") or ""})
    return segs or None


def _to_message_item(m) -> dict:
    """Mongo AgentMessage → 前端字段（与旧 MySQL 版响应同形，前端零改动）"""
    return {
        "message_id": m.id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content or "",
        "thinking": m.thinking,
        "thinking_ms": m.thinking_ms,
        "tool_calls": [tc.model_dump() for tc in (m.tool_calls or [])],
        "usage": m.usage.model_dump() if m.usage else None,
        "attachments": m.attachments or [],
        "finish_reason": m.finish_reason,
        "create_time": m.create_time.isoformat() if m.create_time else None,
        "segments": _build_segments(m),
    }


class CreateSessionBody(BaseModel):
    title: str = Field(default="新会话", max_length=255)
    model: str | None = None
    thinking_level: str = "off"


class RenameSessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)


def _owned_or_404(session_id: str, user_id: str) -> dict:
    info = agent_session_repo.get_session(session_id)
    if info is None or info["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="session not found")
    return info


@router.post("", summary="创建会话")
async def create_session(
    body: CreateSessionBody,
    user_id: str = Depends(get_current_user_id),
):
    info = await asyncio.to_thread(
        agent_session_repo.create_session,
        user_id=user_id,
        title=body.title,
        model=body.model or None,
        thinking_level=body.thinking_level,
    )
    return {"code": 0, "message": "ok", "data": info}


@router.get("", summary="会话列表（活跃度倒序）")
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
):
    items, total = await asyncio.to_thread(
        agent_session_repo.list_sessions,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }


@router.get("/{session_id}", summary="会话详情")
async def get_session(
    session_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    info = await asyncio.to_thread(_owned_or_404, session_id, user_id)
    return {"code": 0, "message": "ok", "data": info}


@router.patch("/{session_id}", summary="重命名会话")
async def rename_session(
    body: RenameSessionBody,
    session_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    await asyncio.to_thread(_owned_or_404, session_id, user_id)
    info = await asyncio.to_thread(
        agent_session_repo.rename_session,
        session_id=session_id,
        title=body.title,
        user_id=user_id,
    )
    return {"code": 0, "message": "ok", "data": info}


@router.delete("/{session_id}", summary="软删会话")
async def delete_session(
    session_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    await asyncio.to_thread(_owned_or_404, session_id, user_id)
    ok = await asyncio.to_thread(
        agent_session_repo.soft_delete_session,
        session_id=session_id,
        user_id=user_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"code": 0, "message": "ok", "data": {"session_id": session_id}}


@router.get("/{session_id}/messages", summary="消息回放（正序分页）")
async def list_messages(
    session_id: str = Path(min_length=1, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
):
    await asyncio.to_thread(_owned_or_404, session_id, user_id)
    rows = await agent_message_repo.list_by_session(
        session_id, limit=page_size, skip=(page - 1) * page_size
    )
    total = await agent_message_repo.count_by_session(session_id)
    items = [_to_message_item(m) for m in rows]
    return {
        "code": 0,
        "message": "ok",
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }


@router.delete("/{session_id}/messages", summary="清空消息（会话保留）")
async def clear_messages(
    session_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    await asyncio.to_thread(_owned_or_404, session_id, user_id)
    await agent_message_repo.soft_delete_by_session(session_id, updater=user_id)
    await asyncio.to_thread(
        agent_session_repo.reset_message_count,
        session_id=session_id,
        user_id=user_id,
    )
    return {"code": 0, "message": "ok", "data": {"session_id": session_id}}
