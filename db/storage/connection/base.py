#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : base.py（db/storage/connection）
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    对象存储连接管理层基类与异常（对齐 db/mysql/connection 的分层职责）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from minio import Minio


class StorageConnectionError(Exception):
    """对象存储连接/配置错误（缺 env、客户端初始化失败）"""


class StorageObjectError(Exception):
    """对象操作失败（上传/下载/presign/删除）；NoSuchKey 归一化为 not_found 语义"""


class BaseStorageManager:
    """
    对象存储连接管理器基类：持有客户端单例 + 桶约定。

    与 MySQLManager 的分工对应：connection 层只管连接与客户端生命周期，
    对象语义（键规则/工作空间读写）在 models/ 与 repositories/ 层。
    """

    client: Minio
    bucket: str

    def health_check(self) -> bool:
        raise NotImplementedError
