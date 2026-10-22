#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : files.py（api/routes/v1）
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    会话工作空间文件 REST（设计 §6.1）。

    POST   /api/v1/sessions/{sid}/files   multipart 上传 → 202 {file_id, kind, parse_status}
    GET    /api/v1/sessions/{sid}/files   附件列表（含解析状态，前端轮询用，D15 纯轮询）
    POST   /api/v1/uploads                临时区上传（会话未创建时，D1）
    GET    /api/v1/files/{fid}            元数据
    GET    /api/v1/files/{fid}/raw        原始文件流（鉴权后）
    GET    /api/v1/files/{fid}/markdown   解析产物（文档类）
    POST   /api/v1/files/{fid}/asset-urls md 内图片批量换 presigned URL（D14）
    DELETE /api/v1/files/{fid}            删除

    归属校验：file.user_id == 当前用户（复用 sessions 的属主语义）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.dependencies.auth import get_current_user_id
from db.mysql.repositories import agent_session_file_repo as file_repo
from src.service.workspace import file_views
from src.service.workspace.file_types import FileRejectError
from src.service.workspace.upload_service import UploadError, accept_upload
from db.storage.connection.base import StorageObjectError
from db.storage.models import build_asset_key, build_raw_key
from db.storage.repositories import (
    get_object,
    list_prefix,
    presign_object,
    stat_object_size,
    stream_object,
)

router = APIRouter(tags=["Workspace Files"])

# presigned 有效期（D14：渲染换链，短 TTL）
ASSET_URL_TTL_SECONDS = 900


def _owned_file_or_404(file_id: str, user_id: str) -> dict:
    info = file_repo.get_file(file_id)
    if info is None or info["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="file not found")
    return info


@router.post("/api/v1/sessions/{session_id}/files", status_code=202, summary="会话内上传附件")
async def upload_session_file(
    file: UploadFile,
    session_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    from db.mysql.repositories import agent_session_repo

    owned = await asyncio.to_thread(agent_session_repo.get_session, session_id)
    if owned is None or owned["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="session not found")

    data = await file.read()
    try:
        created = await accept_upload(
            data=data,
            name=file.filename or "unnamed",
            user_id=user_id,
            session_id=session_id,
            declared_mime=(file.content_type or "").split(";")[0].strip(),
        )
    except UploadError as e:
        # 拒绝类（可执行/超限/坏图片）400；存储类失败 502
        msg = str(e)
        code = 502 if ("上传失败" in msg or "对象" in msg) else 400
        raise HTTPException(status_code=code, detail=msg) from e
    except FileRejectError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"code": 0, "message": "ok", "data": created}


@router.post("/api/v1/uploads", status_code=202, summary="临时区上传（会话未创建时，D1）")
async def upload_temp_file(
    file: UploadFile,
    user_id: str = Depends(get_current_user_id),
):
    data = await file.read()
    try:
        created = await accept_upload(
            data=data,
            name=file.filename or "unnamed",
            user_id=user_id,
            session_id=None,
            declared_mime=(file.content_type or "").split(";")[0].strip(),
        )
    except UploadError as e:
        msg = str(e)
        code = 502 if ("上传失败" in msg or "对象" in msg) else 400
        raise HTTPException(status_code=code, detail=msg) from e
    except FileRejectError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"code": 0, "message": "ok", "data": created}


@router.get("/api/v1/sessions/{session_id}/files", summary="会话附件列表（轮询解析状态）")
async def list_session_files(
    session_id: str = Path(min_length=1, max_length=64),
    origin: str | None = None,
    user_id: str = Depends(get_current_user_id),
):
    from db.mysql.repositories import agent_session_repo

    owned = await asyncio.to_thread(agent_session_repo.get_session, session_id)
    if owned is None or owned["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="session not found")
    items = await asyncio.to_thread(file_repo.list_by_session, session_id, origin=origin)
    return {"code": 0, "message": "ok", "data": {"items": items, "total": len(items)}}


@router.get("/api/v1/files/{file_id}", summary="文件元数据")
async def get_file_meta(
    file_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    info = await asyncio.to_thread(_owned_file_or_404, file_id, user_id)
    return {"code": 0, "message": "ok", "data": info}


@router.get("/api/v1/files/{file_id}/raw", summary="原始文件流（下载/预览）")
async def get_file_raw(
    file_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    info = await asyncio.to_thread(_owned_file_or_404, file_id, user_id)
    obj_session = info["session_id"] or f"temp-{file_id}"
    key = build_raw_key(info["user_id"], obj_session, file_id)
    try:
        size = await stat_object_size(key)
    except StorageObjectError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if size is None:
        raise HTTPException(status_code=404, detail="object missing")

    filename = urllib.parse.quote(info["name"] or file_id)
    is_image = (info["kind"] == "image")
    disposition = "inline" if is_image else "attachment"
    return StreamingResponse(
        stream_object(key),
        media_type=info["mime"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
            "Content-Length": str(size),
        },
    )


@router.get("/api/v1/files/{file_id}/markdown", summary="解析产物 markdown（文档类）")
async def get_file_markdown(
    file_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    info = await asyncio.to_thread(_owned_file_or_404, file_id, user_id)
    if not info.get("md_object_key"):
        raise HTTPException(status_code=404, detail="no parsed markdown for this file")
    from src.service.workspace.upload_service import read_workspace_object

    try:
        data = await read_workspace_object(info, "md")
    except StorageObjectError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "code": 0,
        "message": "ok",
        "data": {"file_id": file_id, "chars": info.get("md_chars"), "markdown": data.decode("utf-8", errors="replace")},
    }


class AssetUrlsBody(BaseModel):
    names: list[str] = Field(default_factory=list, description="资产名列表；空 = 全部资产")


@router.post("/api/v1/files/{file_id}/asset-urls", summary="md 图片资产批量换 presigned URL（D14）")
async def get_file_asset_urls(
    body: AssetUrlsBody,
    file_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    info = await asyncio.to_thread(_owned_file_or_404, file_id, user_id)
    if not info.get("md_object_key"):
        raise HTTPException(status_code=404, detail="no parsed markdown for this file")
    obj_session = info["session_id"] or f"temp-{file_id}"

    if body.names:
        names = body.names
    else:
        # 默认全量：从 md 反查资产名，缺省则扫前缀兜底
        try:
            md = (await get_object(info["md_object_key"])).decode("utf-8", errors="replace")
            names = file_views.extract_asset_names(md)
        except StorageObjectError:
            names = []
        if not names:
            prefix = build_asset_key(info["user_id"], obj_session, file_id, "")
            prefix = prefix[: prefix.rfind("/") + 1]
            names = [
                n.rsplit("/", 1)[-1]
                for n in await list_prefix(prefix)
            ]

    urls: dict[str, str] = {}
    for name in names[:50]:  # 单文件资产数防御上限
        key = build_asset_key(info["user_id"], obj_session, file_id, name)
        try:
            urls[name] = await presign_object(key, ASSET_URL_TTL_SECONDS)
        except StorageObjectError as e:
            logger.warning(f"[workspace] presign 失败跳过: {name}, {e}")
    return {"code": 0, "message": "ok", "data": {"file_id": file_id, "urls": urls}}


@router.delete("/api/v1/files/{file_id}", summary="删除附件（软删）")
async def delete_file(
    file_id: str = Path(min_length=1, max_length=64),
    user_id: str = Depends(get_current_user_id),
):
    await asyncio.to_thread(_owned_file_or_404, file_id, user_id)
    ok = await asyncio.to_thread(file_repo.soft_delete_file, file_id=file_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="file not found")
    return {"code": 0, "message": "ok", "data": {"file_id": file_id}}
