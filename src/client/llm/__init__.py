#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps（同构自 folio-aks-server，改动请双侧同步）
@File    : __init__.py（src/client/llm）
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    LLM 模型注册中心导出（agent-server 版）。
    与 AKS 的差异：不含 LLMClient（模型调用由 openai-agents SDK 托管，
    见 src/runtime/model_client.py）；registry / thinking_adapter 与 AKS
    同构复制，文件级 diff 可同步。
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from src.client.llm.registry import (
    LLMModelInfo,
    LiteLLMRegistry,
    get_litellm_registry,
)
from src.client.llm.thinking_adapter import get_thinking_adapter

__all__ = [
    "LLMModelInfo",
    "LiteLLMRegistry",
    "get_litellm_registry",
    "get_thinking_adapter",
]
