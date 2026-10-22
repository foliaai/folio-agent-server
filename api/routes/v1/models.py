#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : models.py（api/routes/v1）
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    模型清单 REST 路由（前端模型选择器数据源，响应字段与 AKS 的
    /api/chat/models 同形——前端组件可零改动复用）。
    GET /api/v1/models?refresh=false
    POST /api/v1/models/refresh   强制刷新缓存
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from fastapi import APIRouter, Depends, Query
from loguru import logger

from api.dependencies.auth import get_current_user_id
from src.client.llm import get_litellm_registry

router = APIRouter(tags=["Models"])


def _serialize(models):
    """LLMModelInfo → 前端字段（thinking_level_map 是内部翻译表，不下发）"""
    return [
        {
            "id": m.id,
            "label": m.label,
            "provider": m.provider,
            "supports_thinking": m.supports_thinking,
            "thinking_levels": m.thinking_levels,
            "default_thinking_level": m.default_thinking_level,
            "supports_multimodal": m.supports_multimodal,
            "max_context": m.max_context,
        }
        for m in models
    ]


@router.get("", summary="拉取可见模型清单")
async def list_models(
    refresh: bool = Query(False, description="是否强制刷新（绕过 5min TTL 缓存）"),
    user_id: str = Depends(get_current_user_id),
):
    """当前档案白名单 × 网关库存的交集；网关不可达时降级复用上次成功结果"""
    items = get_litellm_registry().list_models(force_refresh=refresh)
    return {"code": 0, "message": "ok", "data": {"models": _serialize(items)}}


@router.post("/refresh", summary="强制刷新模型清单缓存")
async def refresh_models(user_id: str = Depends(get_current_user_id)):
    items = get_litellm_registry().list_models(force_refresh=True)
    logger.info(f"model registry refreshed by {user_id}: {len(items)} models")
    return {"code": 0, "message": "ok", "data": {"count": len(items), "models": _serialize(items)}}
