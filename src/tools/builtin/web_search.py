#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : web_search.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    web_search 工具（Tavily）。无需用户上下文（全局 API key）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from agents import function_tool
from loguru import logger

from src.utils.env_manager import get_env_manager


@function_tool
async def web_search(query: str, max_results: int = 5) -> str:
    """搜索公开网络信息。适用于需要最新事实、外部资料、实时数据的场景。

    Args:
        query: 搜索关键词（通常用中英文均可）。
        max_results: 返回结果条数（1-10，默认 5）。
    """
    env = get_env_manager()
    api_key = env.get_tavily_api_key()
    if not api_key:
        return "web_search 不可用：平台未配置 TAVILY_API_KEY"

    try:
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=api_key)
        resp = await client.search(query=query, max_results=max(1, min(max_results, 10)))
    except Exception as e:  # noqa: BLE001 —— 工具失败不拖垮 loop，返回可读错误
        logger.warning(f"web_search failed: {e}")
        return f"web_search failed: {e}"

    lines = []
    for i, item in enumerate(resp.get("results", []), 1):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("content", "")[:400]
        lines.append(f"[{i}] {title}\n{url}\n{snippet}")
    return "\n\n".join(lines) or "no results"
