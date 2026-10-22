#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : model_client.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    模型接入（架构方案 D5）：唯一入口 = 模型网关（litellm / model_lake 双模式，
    与 AKS 同名环境变量，见 src/client/llm/gateway.py）。
      - AsyncOpenAI 客户端进程内单例（连接池复用）
      - 显式 ChatCompletions 模型类（SDK 默认 Responses API 依赖 proxy 桥接，不赌）
      - 模型清单/白名单/思考档位翻译在 src/client/llm/registry.py（与 AKS 同构）
@Modify History:
    2026-10-03 双网关支持 + registry 白名单（对齐 AKS 的多模型能力）

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Optional

from agents import OpenAIChatCompletionsModel
from loguru import logger
from openai import AsyncOpenAI

from src.client.llm.gateway import (
    get_llm_gateway_full_config,
    get_model_gateway_type,
)

# 默认模型（裸名；归一化后经 registry 清单校验）
# normalize 后形式（model_lake 需 channel/model；litellm 为裸名）。
# 须与 /api/v1/models 清单项对应（网关库存变化时同步）。
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

_client: Optional[AsyncOpenAI] = None


def get_openai_client() -> AsyncOpenAI:
    """模型网关的 AsyncOpenAI 客户端（单例；litellm / model_lake 通用 OpenAI 兼容协议）"""
    global _client
    if _client is None:
        cfg = get_llm_gateway_full_config()
        base = (cfg.get("api_base") or "").strip().rstrip("/")
        key = (cfg.get("api_key") or "").strip() or "sk-noop"
        if not base:
            raise RuntimeError(
                "未配置模型网关 base（检查 .env: MODEL_LAKE_BASE / LITELLM_PROXY_URL）"
            )
        # AsyncOpenAI 需要 base 不带 /v1（SDK 自动拼 /chat/completions 等）
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        _client = AsyncOpenAI(
            base_url=f"{base}/v1",
            api_key=key,
            timeout=cfg.get("default_timeout", 300.0),
        )
        logger.info(f"model client -> {cfg.get('gateway_type')} gateway @ {base}")
    return _client


def normalize_model_id(model: Optional[str]) -> Optional[str]:
    """
    把用户/前端传入的模型 id 归一化为**直连网关的 HTTP model 字符串**。

    registry 清单 id 是 litellm SDK 形态（``litellm_proxy/<bare>`` /
    ``openai/<channel>/<model>``）——那是 litellm Python 库的路由约定；
    本服务经 openai SDK 直连网关，HTTP 请求的 model 字段要用：
    - litellm 模式：裸名（proxy 配置的 model_name）
    - model_lake 模式：去掉 ``openai/`` 前缀的原始路由（channel/model）
    """
    from src.client.llm.registry import LiteLLMRegistry

    raw = (model or "").strip()
    if not raw:
        return None
    gw = get_model_gateway_type()
    if gw in ("model_lake", "openai", "openai_compatible"):
        stripped = raw[len("openai/"):] if raw.startswith("openai/") else raw
        return stripped or None
    return LiteLLMRegistry._bare_model_name(raw) or None


def is_model_allowed(model_id: str) -> bool:
    """模型是否在 registry 清单（visible 白名单 × 网关库存）内。"""
    from src.client.llm.registry import LiteLLMRegistry, get_litellm_registry

    allowed = {
        LiteLLMRegistry._strip_gateway_prefix(m.id)
        for m in get_litellm_registry().list_models()
    }
    return LiteLLMRegistry._strip_gateway_prefix(model_id) in allowed


def get_chat_model(model: Optional[str] = None) -> OpenAIChatCompletionsModel:
    """构造 SDK 的 ChatCompletions 模型实例（供 Agent(model=...) 使用）"""
    normalized = normalize_model_id(model) or DEFAULT_MODEL  # DEFAULT_MODEL 已是裸名
    return OpenAIChatCompletionsModel(
        model=normalized,
        openai_client=get_openai_client(),
    )
