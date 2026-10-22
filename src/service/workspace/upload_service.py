#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : upload_service.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    上传编排 + 后台解析管线（设计 §4）。

    入口 accept_upload()：分型嗅探 → 硬限制校验（D10）→ 先行后对象（D9 写序）
    → 按路由收尾：
      image     → 压缩（长边 2048 / JPEG q85，D7）覆写对象，直接 ready
      direct_md → 内容即解析产物（{fid}.md），ready
      raw       → 仅原始对象，ready
      mineru    → pending → 后台任务（submit → 轮询 → 图片资产 → md 重写落盘）→ ready/failed

    状态语义（D2/D15）：前端轮询 GET files 列表；failed 可删可重传，不自动重试（D21）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from db.mysql.repositories import agent_session_file_repo as file_repo
from src.client import mineru_client
from src.service.workspace import file_types
from src.service.workspace.file_views import (
    rebuild_md_from_content_list,
    rewrite_md_image_links,
)
from db.storage.connection.base import StorageObjectError
from db.storage.models import (
    build_asset_key,
    build_md_key,
    build_raw_key,
    build_temp_prefix,
)
from db.storage.repositories import (
    delete_prefix,
    get_object,
    get_object_any,
    list_prefix,
    put_object,
)
from src.utils.config_manager import get_config_manager

# 图片压缩参数（D7：长边 2048 / JPEG q85；PNG 保留透明通道不转 JPEG）
IMAGE_MAX_LONG_EDGE = 2048
JPEG_QUALITY = 85


class UploadError(Exception):
    """上传被拒或失败（REST 层转 4xx/5xx）"""


def _workspace_cfg() -> Dict[str, Any]:
    cm = get_config_manager()
    return {
        "max_file_mb": int(cm.get("workspace.upload_max_file_mb", 50)),
        "max_files_per_session": int(cm.get("workspace.upload_max_files_per_session", 20)),
    }


def compress_image(data: bytes, name: str) -> tuple[bytes, str]:
    """
    图片压缩：长边 > 2048 等比缩小；有损格式（jpeg/webp/gif 首帧）转 JPEG q85，
    PNG 保留 PNG（透明通道）。过大的图压回 2048 以内控制 base64 成本（D7）。

    Returns:
        (压缩后字节, mime)
    """
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise UploadError(f"图片内容无法解析: {name}") from e

    keep_png = (img.mode in ("RGBA", "LA", "P")) or name.lower().endswith(".png")
    if keep_png and img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    long_edge = max(img.size)
    if long_edge > IMAGE_MAX_LONG_EDGE:
        scale = IMAGE_MAX_LONG_EDGE / float(long_edge)
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )

    buf = BytesIO()
    if keep_png:
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        mime = "image/jpeg"
    return buf.getvalue(), mime


async def accept_upload(
    *,
    data: bytes,
    name: str,
    user_id: str,
    session_id: Optional[str],
    declared_mime: str = "",
) -> Dict[str, Any]:
    """
    上传一个文件进工作空间（会话内 / 临时区共用入口）。

    Returns:
        目录行 info（含 file_id / kind / parse_status）。
    Raises:
        UploadError: 可执行拒绝（D10）/ 超限 / 存储失败（行已回滚）。
    """
    cfg = _workspace_cfg()
    if len(data) > cfg["max_file_mb"] * 1024 * 1024:
        raise UploadError(f"文件超过单文件上限 {cfg['max_file_mb']}MB: {name}")
    if not data:
        raise UploadError("空文件")

    head = data[:64]
    kind, route, mime = file_types.route_file(head, name, declared_mime)

    if session_id:
        count = await asyncio.to_thread(file_repo.count_session_files, session_id)
        if count >= cfg["max_files_per_session"]:
            raise UploadError(
                f"会话附件数已达上限（{cfg['max_files_per_session']} 个），请先删除部分附件"
            )

    # D9 写序：先行后对象（悬空行自愈；反序会产生扫不出的孤儿对象）
    info = await asyncio.to_thread(
        file_repo.create_file,
        user_id=user_id,
        session_id=session_id,
        kind=kind,
        origin="upload",
        name=name,
        mime=mime,
        size=len(data),
        parse_status="pending",
    )
    file_id = info["file_id"]

    try:
        # 临时区行还没绑定会话：对象先放 user 前缀下的 temp 段，绑定时搬运归位
        obj_session = session_id or f"temp-{file_id}"
        raw_key = build_raw_key(user_id, obj_session, file_id)

        if route == "image":
            data, final_mime = await asyncio.to_thread(compress_image, data, name)
            await put_object(raw_key, data, content_type=final_mime)
            await asyncio.to_thread(
                file_repo.update_parse_status,
                file_id=file_id,
                parse_status="ready",
            )
            info.update(parse_status="ready", size=len(data), mime=final_mime)

        elif route == "direct_md":
            await put_object(raw_key, data, content_type=mime)
            text = data.decode("utf-8", errors="replace")
            md_key = build_md_key(user_id, obj_session, file_id)
            await put_object(md_key, text.encode("utf-8"), content_type="text/markdown")
            await asyncio.to_thread(
                file_repo.update_parse_status,
                file_id=file_id,
                parse_status="ready",
                md_object_key=md_key,
                md_chars=len(text),
            )
            info.update(parse_status="ready", md_chars=len(text))

        elif route == "raw":
            await put_object(raw_key, data, content_type=mime)
            await asyncio.to_thread(
                file_repo.update_parse_status,
                file_id=file_id,
                parse_status="ready",
            )
            info.update(parse_status="ready")

        else:  # mineru
            await put_object(raw_key, data, content_type=mime)
            await asyncio.to_thread(
                file_repo.update_parse_status, file_id=file_id, parse_status="parsing"
            )
            info.update(parse_status="parsing")
            # 后台任务：分钟级，立刻返回让前端轮询（§4.2）
            asyncio.create_task(_run_mineru_parse(file_id, user_id, obj_session, name))

        return info

    except Exception as e:  # noqa: BLE001 —— 上传半途失败：回滚目录行，不留悬空
        await asyncio.to_thread(file_repo.purge_file_row, file_id)
        if isinstance(e, StorageObjectError):
            raise UploadError(str(e)) from e
        raise UploadError(f"上传失败: {e}") from e


async def _run_mineru_parse(file_id: str, user_id: str, obj_session: str, name: str) -> None:
    """后台 MinerU 解析（§4.2）：submit → wait → 图片资产上传 + md 重写 → ready"""
    raw_key = build_raw_key(user_id, obj_session, file_id)
    try:
        data = await get_object(raw_key)
        task_id = await mineru_client.submit_task(data, name)
        await mineru_client.wait_task(task_id)
        result = await mineru_client.fetch_result(task_id)

        # 解析文本唯一装配路径：从 content_list 重建（语义标签 + 表内公式转
        # math 元素 + text_level 标题）；重建为空（content_list 缺失的退化
        # 场景）时原样落现成 md
        md = rebuild_md_from_content_list(result.get("content_list")) or (result["md"] or "")
        md = rewrite_md_image_links(md)
        uploaded_assets = 0
        for img_name, img_bytes in result["images"].items():
            if not img_bytes:
                continue
            await put_object(
                build_asset_key(user_id, obj_session, file_id, img_name),
                img_bytes,
                content_type="image/png",
            )
            uploaded_assets += 1

        md_key = build_md_key(user_id, obj_session, file_id)
        await put_object(md_key, md.encode("utf-8"), content_type="text/markdown")
        await asyncio.to_thread(
            file_repo.update_parse_status,
            file_id=file_id,
            parse_status="ready",
            md_object_key=md_key,
            md_chars=len(md),
            asset_count=uploaded_assets,
        )
        logger.info(
            f"[workspace] MinerU 解析完成: {name} file={file_id} "
            f"chars={len(md)} assets={uploaded_assets}"
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"[workspace] MinerU 解析失败: {name} file={file_id}, {e}")
        await asyncio.to_thread(
            file_repo.update_parse_status,
            file_id=file_id,
            parse_status="failed",
            parse_error=str(e)[:2000],
        )


async def bind_temp_attachments(*, file_ids: List[str], user_id: str, session_id: str) -> List[Dict]:
    """start 帧绑定临时区附件（D1）：目录行回填 session_id + 对象从 temp- 前缀搬运归位"""
    bound = await asyncio.to_thread(
        file_repo.bind_temp_files, file_ids=file_ids, user_id=user_id, session_id=session_id
    )
    if bound == 0:
        return []
    moved: List[Dict] = []
    infos = []
    for fid in file_ids:
        info = await asyncio.to_thread(file_repo.get_file, fid)
        if info is not None:
            infos.append(info)
    for info in infos:
        fid = info["file_id"]
        temp_prefix = build_temp_prefix(user_id, fid)
        # 对象搬运：raw / md / assets 都从 temp- 前缀复制到会话前缀后删源。
        # 注意 new_prefix 不含 fid——obj_name 去掉 temp 前缀后自带 /{fid} 段
        new_prefix = f"{user_id}/{session_id}"
        try:
            names = await list_prefix(temp_prefix)
            for obj_name in names:
                relocated = new_prefix + obj_name[len(temp_prefix):]
                data = await get_object(obj_name)
                await put_object(relocated, data, content_type="application/octet-stream")
            if names:
                await delete_prefix(temp_prefix)
            # md_object_key 指向 temp 前缀的行改指会话前缀（搬运成功的收尾）
            old_md = info.get("md_object_key")
            if old_md and old_md.startswith(temp_prefix):
                await asyncio.to_thread(
                    file_repo.update_parse_status,
                    file_id=fid,
                    parse_status="ready",
                    md_object_key=new_prefix + old_md[len(temp_prefix):],
                )
            moved.append(dict(info, session_id=session_id))
        except Exception as e:  # noqa: BLE001 —— 搬运失败不阻断对话：行已绑定，读取走 get_any 回退 temp 键
            logger.warning(f"[workspace] 临时区对象搬运失败（读取回退 temp 键）: {fid}, {e}")
            moved.append(dict(info, session_id=session_id))
    return moved


async def read_workspace_object(info: Dict, which: str = "raw") -> bytes:
    """
    按目录行读工作空间对象（会话键优先，temp- 键回退）。

    统一读取入口（raw / md 两类键）：已绑定的临时区文件在搬运失败窗口期、
    以及 md_object_key 尚指向 temp 前缀的行，都能经回退读到。调用方一律
    用本函数，不直接拼 store 键。
    """
    fid = info["file_id"]
    builder = build_raw_key if which == "raw" else build_md_key
    obj_session = info["session_id"] or f"temp-{fid}"
    keys = [builder(info["user_id"], obj_session, fid)]
    if info["session_id"]:
        keys.append(builder(info["user_id"], f"temp-{fid}", fid))
    return await get_object_any(keys)
