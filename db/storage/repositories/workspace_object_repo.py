#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : workspace_object_repo.py（db/storage/repositories）
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    会话工作空间对象 repository（MinIO，对齐 db/*/repositories 的职责分层：
    connection 层管客户端，models 层管键规则，本层管对象读写语义）。

    全部 async（minio-py 同步客户端经 asyncio.to_thread 包装，不阻塞事件
    循环）。NoSuchKey 归一化为 StorageObjectError（"对象不存在"文本约定，
    get_object_any 依赖它做多键回退）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
from io import BytesIO
from typing import Iterator, List, Optional

from loguru import logger
from minio.error import S3Error

from db.storage.connection.base import StorageObjectError
from db.storage.connection.minio_manager import get_storage_manager


async def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """上传对象（整载内存，工作空间对象 ≤50MB 上限内可接受）"""
    mgr = get_storage_manager()

    def _put() -> None:
        mgr.client.put_object(
            mgr.bucket, key, BytesIO(data), length=len(data), content_type=content_type
        )

    try:
        await asyncio.to_thread(_put)
    except Exception as e:  # noqa: BLE001
        raise StorageObjectError(f"对象上传失败 {key}: {e}") from e


async def get_object(key: str) -> bytes:
    """下载对象全文；不存在时抛 StorageObjectError（"对象不存在"前缀）"""

    def _get() -> bytes:
        mgr = get_storage_manager()
        resp = mgr.client.get_object(mgr.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    try:
        return await asyncio.to_thread(_get)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise StorageObjectError(f"对象不存在 {key}") from e
        raise StorageObjectError(f"对象下载失败 {key}: {e}") from e
    except StorageObjectError:
        raise
    except Exception as e:  # noqa: BLE001
        raise StorageObjectError(f"对象下载失败 {key}: {e}") from e


async def get_object_any(keys: List[str]) -> bytes:
    """按序尝试多个键，取第一个存在的对象（临时区搬运失败窗口的回退读）"""
    last_err: Optional[Exception] = None
    for key in keys:
        try:
            return await get_object(key)
        except StorageObjectError as e:
            last_err = e
    raise last_err or StorageObjectError("no keys provided")


def stream_object(key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """同步流式下载（FastAPI StreamingResponse 会放到线程池执行）"""
    mgr = get_storage_manager()
    resp = mgr.client.get_object(mgr.bucket, key)
    try:
        for chunk in resp.stream(amt=chunk_size):
            yield chunk
    finally:
        try:
            resp.close()
            resp.release_conn()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[storage] 释放流式响应忽略异常: {e}")


async def stat_object_size(key: str) -> Optional[int]:
    """对象字节数；不存在返回 None（raw 端点 Content-Length 用）"""

    def _stat() -> Optional[int]:
        mgr = get_storage_manager()
        try:
            obj = mgr.client.stat_object(mgr.bucket, key)
            return int(getattr(obj, "size", 0) or 0)
        except S3Error:
            return None

    return await asyncio.to_thread(_stat)


async def presign_object(key: str, expires_seconds: int = 900) -> str:
    """生成 presigned GET URL（asset-urls 端点，短 TTL）"""
    from datetime import timedelta

    mgr = get_storage_manager()

    def _presign() -> str:
        return mgr.client.presigned_get_object(
            mgr.bucket, key, expires=timedelta(seconds=expires_seconds)
        )

    try:
        return await asyncio.to_thread(_presign)
    except Exception as e:  # noqa: BLE001
        raise StorageObjectError(f"presigned 生成失败 {key}: {e}") from e


async def list_prefix(prefix: str) -> List[str]:
    """列举前缀下全部对象名（资产枚举 / 临时区搬运）"""
    mgr = get_storage_manager()

    def _list() -> List[str]:
        return [
            obj.object_name
            for obj in mgr.client.list_objects(mgr.bucket, prefix=prefix, recursive=True)
        ]

    try:
        return await asyncio.to_thread(_list)
    except Exception as e:  # noqa: BLE001
        raise StorageObjectError(f"前缀列举失败 {prefix}: {e}") from e


async def delete_prefix(prefix: str) -> int:
    """删除前缀下全部对象（delete-if-exists 语义，返回删除数）。
    逐个 remove_object——minio-py 7.2.20 的批量 remove_objects 在本实例上
    报 'str' object has no attribute 'toxml'，放弃批量接口。"""

    def _delete() -> int:
        mgr = get_storage_manager()
        names = [
            obj.object_name
            for obj in mgr.client.list_objects(mgr.bucket, prefix=prefix, recursive=True)
        ]
        deleted = 0
        for name in names:
            try:
                mgr.client.remove_object(mgr.bucket, name)
                deleted += 1
            except S3Error as e:
                if e.code != "NoSuchKey":
                    logger.warning(f"[storage] 删除对象失败（跳过）: {name}, {e}")
        return deleted

    try:
        return await asyncio.to_thread(_delete)
    except Exception as e:  # noqa: BLE001
        raise StorageObjectError(f"前缀删除失败 {prefix}: {e}") from e
