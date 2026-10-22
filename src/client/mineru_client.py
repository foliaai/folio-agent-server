#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : mineru_client.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    MinerU 薄客户端（D3：共用 AKS 侧实例，本服务自持直连；设计 §4.2）。

    与 AKS Mineru2Client 的关系：同一任务队列 API（submit → 轮询 → data），
    但 ① httpx 异步（AKS 版是 requests 同步，会卡事件循环）；
    ② 不做 content_list 分块转换（那是 AKS 索引管线的消费形态，本服务
    只取现成 markdown + 图片 base64，设计 §4.4）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
import base64
from typing import Dict

import httpx
from loguru import logger

from src.utils.config_manager import get_config_manager
from src.utils.env_manager import get_env_manager


class MineruError(Exception):
    """MinerU 任务失败/超时/网络错误（parse_pipeline 转 parse_status=failed）"""


def _config() -> Dict:
    cm = get_config_manager()
    env = get_env_manager()
    api_url = (env.get("MINERU_API_URL") or "").strip().rstrip("/")
    if not api_url:
        raise MineruError("MinerU 未配置（检查 .env: MINERU_API_URL）")
    return {
        "api_url": api_url,
        "timeout": float(cm.get("mineru.timeout", 600)),
        "poll_interval": float(cm.get("mineru.poll_interval", 1.0)),
    }


async def submit_task(file_bytes: bytes, file_name: str) -> str:
    """提交解析任务 → task_id（分钟级耗时，调用方在后台任务里等待）"""
    cfg = _config()
    data = {
        "backend": "pipeline",
        "lang": "ch",
        "method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
        "priority": "0",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{cfg['api_url']}/api/v1/tasks/submit",
                files={"file": (file_name, file_bytes)},
                data=data,
            )
            if resp.status_code != 200:
                raise MineruError(f"提交失败 HTTP {resp.status_code}: {resp.text[:300]}")
            task_id = resp.json().get("task_id")
            if not task_id:
                raise MineruError(f"响应缺 task_id: {resp.text[:300]}")
            logger.debug(f"[mineru] 任务已提交: {file_name} -> {task_id}")
            return task_id
    except MineruError:
        raise
    except Exception as e:  # noqa: BLE001
        raise MineruError(f"提交任务错误: {e}") from e


async def wait_task(task_id: str) -> None:
    """轮询等待任务完成；failed/cancelled/超时抛 MineruError"""
    cfg = _config()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg["timeout"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                resp = await client.get(f"{cfg['api_url']}/api/v1/tasks/{task_id}")
                if resp.status_code != 200:
                    raise MineruError(f"查询任务失败 HTTP {resp.status_code}: {resp.text[:300]}")
                payload = resp.json()
                status = payload.get("status")
                if status == "completed":
                    return
                if status in ("failed", "cancelled"):
                    raise MineruError(
                        f"任务{status}: {payload.get('error_message', 'unknown')}"
                    )
                if loop.time() > deadline:
                    raise MineruError(f"任务超时（{cfg['timeout']:.0f}s）: {task_id}")
                await asyncio.sleep(cfg["poll_interval"])
    except MineruError:
        raise
    except Exception as e:  # noqa: BLE001
        raise MineruError(f"轮询错误: {e}") from e


async def fetch_result(task_id: str) -> Dict:
    """
    拉取任务数据并抽出现成品 markdown + 图片字节 + content_list。

    Returns:
        {"md": str, "images": {图片名: bytes}, "content_list": [...]}
        content_list 供上游做题注/脚注语义标注（table_caption/table_footnote/
        img_caption/img_footnote 只在 content_list 里，现成 md 是平文本）。
    """
    cfg = _config()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{cfg['api_url']}/api/v1/tasks/{task_id}/data",
                params={
                    "include_fields": "md,images,content_list",
                    "upload_images": False,
                    "include_image_base64": True,
                    "include_metadata": False,
                },
            )
            if resp.status_code != 200:
                raise MineruError(f"获取数据失败 HTTP {resp.status_code}: {resp.text[:300]}")
            payload = resp.json()
            if payload.get("status") != "completed":
                raise MineruError(f"任务未完成: {payload.get('status')}")
    except MineruError:
        raise
    except Exception as e:  # noqa: BLE001
        raise MineruError(f"获取数据错误: {e}") from e

    data = payload.get("data") or {}
    md = ((data.get("markdown") or {}).get("content")) or ""
    content_list = ((data.get("content_list") or {}).get("content")) or []
    images: Dict[str, bytes] = {}
    for img in (data.get("images") or {}).get("list") or []:
        name, b64 = img.get("name"), img.get("base64")
        if name and b64:
            try:
                images[name] = base64.b64decode(b64)
            except Exception as e:  # noqa: BLE001 —— 单图坏不拖垮整篇
                logger.warning(f"[mineru] 图片解码失败跳过: {name}, {e}")
    return {"md": md, "images": images, "content_list": content_list}
