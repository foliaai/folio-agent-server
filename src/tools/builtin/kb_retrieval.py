#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : kb_retrieval.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    kb_retrieval 工具：HTTP 只读调 AKS 知识库检索（架构方案 §2）。
    用户身份经 ToolDeps.auth_token 透传（Bearer JWT）——检索范围跟随
    用户自己的知识库权限，agent-server 不持有任何越权能力。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import httpx
from agents import RunContextWrapper, function_tool
from loguru import logger

from src.tools.registry import ToolDeps
from src.utils.env_manager import get_env_manager


@function_tool
async def kb_retrieval(
    ctx: RunContextWrapper[ToolDeps],
    query: str,
    top_k: int = 8,
    knowledge_base_id: str = "",
) -> str:
    """检索用户的 folio 知识库（企业/个人文档、资料）。回答涉及已有资料的问题前优先使用。

    Args:
        query: 检索问题（自然语言）。
        top_k: 返回条数（1-20，默认 8）。
        knowledge_base_id: 可选，限定某个知识库 ID；留空则全局检索。
    """
    env = get_env_manager()
    base = env.get_aks_base_url().rstrip("/")
    url = f"{base}/api/knowledge/retrieve"

    payload: dict = {"query_text": query, "top_k": max(1, min(top_k, 20))}
    if knowledge_base_id:
        payload["knowledge_base_id"] = knowledge_base_id

    headers = {}
    if ctx.context and ctx.context.auth_token:
        headers["Authorization"] = f"Bearer {ctx.context.auth_token}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:200] if e.response is not None else ""
        logger.warning(f"kb_retrieval HTTP {e.response.status_code if e.response else '?'}: {detail}")
        return f"kb_retrieval failed: HTTP {e.response.status_code if e.response else '?'}（AKS 可能不可用或 token 无效）"
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"kb_retrieval failed: {e}")
        return f"kb_retrieval failed: {e}"

    # AKS ApiResponse 包装：{code, message, data:{items:[...]}}
    data = body.get("data") or {}
    items = data.get("items") or []
    if not items:
        return "知识库中没有检索到相关内容"

    lines = []
    for i, item in enumerate(items, 1):
        score = item.get("score")
        text = (item.get("text") or "")[:600]
        lines.append(f"[{i}] (score={score:.3f}) {text}" if isinstance(score, float) else f"[{i}] {text}")
    return "\n\n".join(lines)
