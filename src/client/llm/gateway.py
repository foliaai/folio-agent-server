#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps（同构自 folio-aks-server，改动请双侧同步）
@File    : llm_gateway.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    模型网关环境变量读取（litellm / model_lake 双模式，与 AKS 同名同语义）。
    从 env_manager 拆出独立模块：agent-server 的 env_manager 是精简版，
    gateway getter 整段同构复制便于与 AKS 双侧同步。

    环境变量：
    - MODEL_GATEWAY_TYPE      网关类型（litellm | model_lake / openai / openai_compatible）
    - MODEL_LAKE_BASE         ModelNexus base（必须配到 /v1 结尾）
    - MODEL_LAKE_API_KEY      ModelNexus 静态 Key（mn-sk- 开头）
    - LITELLM_PROXY_URL       LiteLLM Proxy base URL
    - LITELLM_PROXY_KEY       LiteLLM Proxy virtual key
    - MODEL_GATEWAY_TIMEOUT   客户端默认超时秒数（默认 60）
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Any, Dict, Optional

from loguru import logger

from src.utils.env_manager import get_env_manager

_MODEL_LAKE_TYPES = ("model_lake", "openai", "openai_compatible")


def get_model_gateway_type() -> str:
    raw = get_env_manager().get("MODEL_GATEWAY_TYPE") or "litellm"
    return raw.strip().lower()


def get_model_gateway_url() -> Optional[str]:
    """网关 base URL：model_lake 系取 MODEL_LAKE_BASE，litellm 取 LITELLM_PROXY_URL"""
    if get_model_gateway_type() in _MODEL_LAKE_TYPES:
        ml_base = get_env_manager().get("MODEL_LAKE_BASE")
        return ml_base.strip().rstrip("/") if ml_base and ml_base.strip() else None
    return get_env_manager().get("LITELLM_PROXY_URL")


def get_model_gateway_key() -> Optional[str]:
    """网关 API Key：model_lake 系取 MODEL_LAKE_API_KEY，litellm 取 LITELLM_PROXY_KEY"""
    if get_model_gateway_type() in _MODEL_LAKE_TYPES:
        raw = get_env_manager().get("MODEL_LAKE_API_KEY")
        return raw.strip() if raw and raw.strip() else None
    return get_env_manager().get("LITELLM_PROXY_KEY")


def get_model_gateway_timeout(default: float = 300.0) -> float:
    raw = get_env_manager().get("MODEL_GATEWAY_TIMEOUT")
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"MODEL_GATEWAY_TIMEOUT 无法转换为数字: {raw}，使用默认值 {default}")
        return default


def get_llm_gateway_full_config() -> Dict[str, Any]:
    """完整网关配置（registry._resolve_proxy_config 的数据源，与 AKS 同构）"""
    return {
        "gateway_type": get_model_gateway_type(),
        "api_base": get_model_gateway_url(),
        "api_key": get_model_gateway_key(),
        "default_timeout": get_model_gateway_timeout(),
        "default_max_retries": 2,
    }
