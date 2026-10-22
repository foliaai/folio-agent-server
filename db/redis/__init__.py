#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : __init__.py
@Author  : caixiongjiang
@Date    : 2026/1/22 15:00
@Function: 
    函数功能名称
@Modify History:
         
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from db.redis.connection import (
    BaseRedisManager,
    StandaloneRedisManager,
    ClusterRedisManager,
    RedisManagerFactory,
    get_redis_manager,
    RedisType,
)
from db.redis.models.namespace import RedisNamespace
from db.redis.models.keys import (
    RedisKeys,
    KeyPattern,
    get_key_pattern,
    register_custom_key,
)

__all__ = [
    # 连接管理器
    "BaseRedisManager",
    "StandaloneRedisManager",
    "ClusterRedisManager",
    "RedisManagerFactory",
    "get_redis_manager",
    "RedisType",
    # 命名空间管理
    "RedisNamespace",
    # Key 管理
    "RedisKeys",
    "KeyPattern",
    "get_key_pattern",
    "register_custom_key",
]