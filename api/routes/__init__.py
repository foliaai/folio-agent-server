#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : __init__.py
@Author  : caixiongjiang
@Date    : 2026/1/21 20:03
@Function:
    路由注册中心 - 统一管理所有路由
@Modify History:
    2026/1/21 - 重构路由结构，分离通用服务和业务应用
    2026/1/23 - 统一路由前缀，所有路由使用 /api/v1 前缀
    2026-10-02 - 旧业务路由删除
    2026-10-03 - M2：挂载首页 agent WebSocket（/api/v1/chat/ws）
=================================================="""

from fastapi import APIRouter

from api.routes.v1.files import router as files_router
from api.routes.v1.models import router as models_router
from api.routes.v1.sessions import router as sessions_router
from api.routes.ws.chat import router as ws_chat_router

# 创建主路由
router = APIRouter()

# WebSocket 路由（/api/v1/chat/ws，与网关约定一致：网关不剥前缀转发）
router.include_router(ws_chat_router, prefix="/api/v1")

# REST 路由（/api/v1/sessions/{sid}/files、/api/v1/files/*、/api/v1/uploads —— 会话附件工作空间）
router.include_router(files_router)

# REST 路由（/api/v1/models，前端模型选择器）
router.include_router(models_router, prefix="/api/v1/models")

# REST 路由（/api/v1/sessions，前端历史会话侧栏）
router.include_router(sessions_router, prefix="/api/v1/sessions")

__all__ = ["router"]
