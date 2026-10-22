#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""对象存储连接管理层（客户端单例 + 桶约定 + 异常基类）"""

from db.storage.connection.base import (
    BaseStorageManager,
    StorageConnectionError,
    StorageObjectError,
)
from db.storage.connection.minio_manager import (
    WORKSPACE_BUCKET,
    MinioManager,
    get_storage_manager,
)

__all__ = [
    "BaseStorageManager",
    "StorageConnectionError",
    "StorageObjectError",
    "WORKSPACE_BUCKET",
    "MinioManager",
    "get_storage_manager",
]
