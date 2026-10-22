#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : minio_manager.py（db/storage/connection）
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    MinIO 连接管理器（进程内单例，对齐 get_mysql_manager / get_mongodb_manager 的角色）。

    - 配置：env 注入（MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY），
      与 MySQL/MongoDB 同款"必填注入"哲学——缺配置在首次获取时 fail fast
    - 桶：工作空间独立桶 agent-workspace（设计 D17，与 AKS 桶隔离），
      管理器初始化时确保存在
    - 客户端：minio-py 同步客户端；上层 repo 以 asyncio.to_thread 包装调用
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Optional

from loguru import logger
from minio import Minio
from minio.error import S3Error

from db.storage.connection.base import BaseStorageManager, StorageConnectionError
from src.utils.env_manager import get_env_manager

# 工作空间桶名（D17：同实例新桶；桶名不允许下划线）
WORKSPACE_BUCKET = "agent-workspace"

_manager: Optional["MinioManager"] = None


class MinioManager(BaseStorageManager):
    """MinIO 客户端管理器（单例经 get_storage_manager 获取）"""

    def __init__(self) -> None:
        env = get_env_manager()
        endpoint = (env.get("MINIO_ENDPOINT") or "").strip()
        access_key = (env.get("MINIO_ACCESS_KEY") or "").strip()
        secret_key = (env.get("MINIO_SECRET_KEY") or "").strip()
        if not endpoint or not access_key or not secret_key:
            raise StorageConnectionError(
                "MinIO 未配置（检查 .env: MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY）"
            )
        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
        )
        self.bucket = WORKSPACE_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """确保工作空间桶存在（权限不足只告警，首次真实读写再暴露）"""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"[storage] 创建 MinIO 桶: {self.bucket}")
        except S3Error as e:
            logger.warning(f"[storage] 桶检查/创建失败（继续）: {e}")

    def health_check(self) -> bool:
        try:
            self.client.bucket_exists(self.bucket)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[storage] MinIO 健康检查失败: {e}")
            return False


def get_storage_manager() -> MinioManager:
    """进程内单例入口（首调初始化并校验配置，缺配置 fail fast）"""
    global _manager
    if _manager is None:
        _manager = MinioManager()
    return _manager
