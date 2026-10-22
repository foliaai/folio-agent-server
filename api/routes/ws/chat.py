#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : chat.py（api/routes/ws）
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    首页 agent WebSocket（/api/v1/chat/ws，经 Caddy 网关 WS 透传）。
    帧协议见 src/runtime/ws_protocol.py（平台协议，对齐 AKS）。
    鉴权：握手 query ?token=<JWT>（auth-core 的 ws 依赖）；
    每连接至多一个进行中 turn（start 期间再 start 会被拒，先 stop）。

    会话（v1.0.0 落库版）：
    - LLM 上下文：进程内存缓存（SessionState.history）+ agent_message 表；
      进程重启后按 session_id 从库重建（load_items）
    - 落库：turn 正常结束后 persist_turn（user + assistant 两行）；
      中断不落（items 可能不完整，persisted_count 不推进）
    - 归属：内存与库两侧都校验 user_id，防跨用户续聊
@Modify History:
    2026-10-04 会话落库（agent_session/agent_message，同构不共表）

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

import base64

from api.dependencies.auth import get_current_user_id_ws
from src.runtime.agent_loop import SessionState, TurnRecord, run_turn
from src.runtime.ws_protocol import (
    ClientFrameType,
    ServerFrameType,
    make_error_frame,
    make_frame,
    parse_client_frame,
)
from src.tools.registry import ToolDeps

router = APIRouter()

# 内存会话表（session_id -> SessionState）：LLM 上下文缓存；持久化在
# agent_message 表，进程重启后按需从库重建（见 start 帧处理）
_sessions: dict[str, SessionState] = {}


def _dev_bypass_enabled() -> bool:
    """本地联调旁路：DEV_AUTH_BYPASS=1 且非 production 环境才生效（auth-server 不在身边的开发机用）"""
    from api.dependencies._env_file_flag import env_file_flag

    # 同 HTTP 侧：唯一事实源 .env 文件（os.environ 有 dotenv 只加不清的残留坑）
    if env_file_flag("DEV_AUTH_BYPASS") not in ("1", "true", "yes"):
        return False
    from src.utils.env_manager import get_env_manager

    if get_env_manager().get_app_env() == "production":
        logger.error("DEV_AUTH_BYPASS is set but APP_ENV=production — refusing")
        return False
    logger.warning("DEV_AUTH_BYPASS active: WS auth bypassed (dev only, identity-aware)")
    return True


async def _dev_bypass_ws_user(ws: WebSocket) -> str | None:
    """
    身份感知旁路（WS 版），与 HTTP 侧同语义：
    - 无 token → dev-user（旁路本职）；
    - 有效 token → 真实 user_id；
    - 带了 token 但无效 → None（调用方 close 4401，静默降级会污染 dev-user）。
    """
    token = (ws.query_params.get("token") or "").strip()
    if not token:
        return "dev-user"
    try:
        from auth_core.verifier import get_verifier

        user = await get_verifier().verify(token)
        return user.user_id
    except Exception as e:  # noqa: BLE001 —— 带了无效凭证：交给调用方拒绝握手
        logger.warning(f"dev bypass ws: token 验签失败，拒绝握手: {e}")
        return None


async def _persist_turn(record: TurnRecord) -> None:
    """
    双库落库（与 AKS 分工一致）：MongoDB 存消息两行，MySQL 推进会话行。
    失败不阻断对话（与 AKS 持久化态度一致：日志告警，流式体验优先）。
    """
    try:
        from db.mongodb.repositories import agent_message_repo
        from db.mysql.repositories import agent_session_repo

        added = await agent_message_repo.insert_turn(
            session_id=record.session_id,
            user_id=record.user_id,
            user_item=record.user_item,
            assistant_items=record.assistant_items,
            attachments=record.attachments or None,
            assistant_content=record.assistant_content,
            thinking=record.thinking,
            thinking_ms=record.thinking_ms,
            tool_calls=record.tool_calls or None,
            usage=record.usage,
            finish_reason=record.finish_reason,
        )
        await asyncio.to_thread(
            agent_session_repo.touch_turn,
            session_id=record.session_id,
            user_id=record.user_id,
            first_message=_user_text(record.user_item),
            added_count=added,
            model=record.model,
            thinking_level=record.thinking_level,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"turn 持久化失败: session={record.session_id}, err={e}")


def _user_text(user_item: dict) -> str:
    """user item content → 展示用纯文本（委托 repo 清洗版：剥存根块/图片占位，
    避免会话标题混入注入文本；items 原样保留给 LLM 装配）"""
    from db.mongodb.repositories.agent_message_repo import AgentMessageRepository

    return AgentMessageRepository._user_text(user_item)


def _model_supports_vision(model: str) -> bool:
    """registry 能力标记查主模型是否支持视觉（路线 A 判定，设计 §5）"""
    try:
        from src.client.llm import get_litellm_registry
        from src.client.llm.registry import LiteLLMRegistry
        from src.runtime.model_client import DEFAULT_MODEL

        target = LiteLLMRegistry._strip_gateway_prefix(model or DEFAULT_MODEL)
        for m in get_litellm_registry().list_models():
            if LiteLLMRegistry._strip_gateway_prefix(m.id) == target:
                return bool(m.supports_multimodal)
    except Exception as e:  # noqa: BLE001 —— 查不到按路线 B（工具看图）兜底
        logger.warning(f"vision 能力查询失败，按路线 B: {e}")
    return False


async def _prepare_attachments(
    file_ids: list[str], user_id: str, session_id: str, model: str
) -> tuple[bool, str | tuple[list, str, list]]:
    """
    start 帧附件就绪检查 + 装配（设计 §6.2/§6.3，D1/D2/D12/D18）。

    Returns:
        (False, 错误文本) 或 (True, (attachments_meta, stubs_block, image_parts))
    """
    import asyncio as _aio

    from db.mysql.repositories import agent_session_file_repo as file_repo
    from src.service.workspace import upload_service
    from src.service.workspace.file_views import (
        build_attachments_block,
        build_file_stub,
        build_md_toc,
    )
    from src.service.workspace.upload_service import read_workspace_object

    # D1：临时区文件绑定到（可能刚生成的）会话 id，对象搬运归位
    try:
        await upload_service.bind_temp_attachments(
            file_ids=file_ids, user_id=user_id, session_id=session_id
        )
    except Exception as e:  # noqa: BLE001 —— 绑定失败按文件校验错误暴露
        return False, f"附件绑定失败: {e}"

    infos = []
    for fid in file_ids:
        info = await _aio.to_thread(file_repo.get_file, fid)
        if info is None or info["user_id"] != user_id:
            return False, f"附件不存在: {fid}"
        if info["session_id"] != session_id:
            return False, f"附件 {info['name']} 不属于当前会话"
        infos.append(info)

    # D2：服务端拒非 ready（防绕过前端锁定）
    for info in infos:
        if info["parse_status"] != "ready":
            why = info["parse_error"] or "解析未完成"
            return False, f"附件 {info['name']} 不可用（{why}），请等解析完成或删除后重传"

    # D18：存根只随本轮注入一次（文件名/类型/大小/字数/摘要）
    vision = _model_supports_vision(model)
    stubs, metas, image_parts = [], [], []
    for info in infos:
        route = "image" if info["kind"] == "image" else ("mineru" if info.get("md_object_key") else "raw")
        md_head = None
        toc = None
        if info.get("md_object_key") and info.get("md_chars"):
            try:
                md = await read_workspace_object(info, "md")
                text = md.decode("utf-8", errors="replace")
                md_head = text[:400]
                toc = build_md_toc(text)
            except Exception:  # noqa: BLE001 —— 摘要/目录失败不影响注入
                md_head = None
        stubs.append(build_file_stub(dict(info, route=route), md_head=md_head, toc=toc))
        metas.append(
            {k: info[k] for k in ("file_id", "kind", "name", "size", "mime") if k in info}
        )
        if vision and info["kind"] == "image":
            try:
                data = await read_workspace_object(info, "raw")
                image_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{info['mime'] or 'image/png'};base64,"
                            f"{base64.b64encode(data).decode('ascii')}"
                        },
                    }
                )
            except Exception as e:  # noqa: BLE001 —— 单图读取失败不阻断整轮，退工具路径
                logger.warning(f"图片读取失败（改走 understand_image）: {info['file_id']}, {e}")

    return True, (metas, build_attachments_block(stubs), image_parts)


@router.websocket("/chat/ws")
async def chat_ws(ws: WebSocket):
    # 鉴权：query token → auth-core 验签；None 时由本方关闭（auth-core 约定）
    if _dev_bypass_enabled():
        user_id = await _dev_bypass_ws_user(ws)
        if user_id is None:
            await ws.close(code=4401, reason="invalid token")
            return
    else:
        user_id = await get_current_user_id_ws(ws)
        if user_id is None:
            await ws.close(code=4401, reason="unauthorized")
            return
    await ws.accept()
    logger.info(f"ws chat connected: user={user_id}")

    current_task: asyncio.Task | None = None

    async def send(frame: dict) -> None:
        try:
            await ws.send_json(frame)
        except Exception:  # noqa: BLE001 —— 断连时静默，由主循环感知
            pass

    async def _run_turn_guarded(
        session: SessionState,
        message: str,
        model: str,
        thinking_level: str,
        deps: ToolDeps,
        attachments: list | None = None,
        stubs_block: str = "",
        image_parts: list | None = None,
    ):
        try:
            await send(make_frame(ServerFrameType.SESSION_READY, session_id=session.session_id))
            record = await run_turn(
                send,
                session,
                message,
                deps,
                model=model or None,
                thinking_level=thinking_level or None,
                attachments=attachments,
                stubs_block=stubs_block,
                image_parts=image_parts,
            )
            # 落库独立 task：turn.done 已发、客户端可能立即发下一轮 start，
            # 不能让持久化阻塞本 task 的完成判定（落库失败自记日志）
            asyncio.create_task(_persist_turn(record))
        except asyncio.CancelledError:
            # 中断不落库：items 可能不完整（persisted_count 未推进，下轮重切）
            await send(make_error_frame("chat_stream", "turn cancelled", {"cancelled": True}))
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception(f"turn crashed: {e}")
            await send(make_error_frame("chat_stream", f"unhandled: {e}"))

    try:
        while True:
            raw = await ws.receive_text()
            frame = parse_client_frame(raw)
            if frame is None:
                await send(make_error_frame("protocol", "malformed frame"))
                continue

            ftype, data = frame["type"], frame["data"]

            if ftype == ClientFrameType.PING:
                await send(make_frame(ServerFrameType.PONG))
                continue

            if ftype == ClientFrameType.STOP:
                if current_task and not current_task.done():
                    current_task.cancel()
                    current_task = None
                else:
                    await send(make_error_frame("stop", "no active turn to stop"))
                continue

            # START
            if current_task and not current_task.done():
                await send(make_error_frame("start", "a turn is already running; send stop first"))
                continue

            message = (data.get("message") or "").strip()
            if not message:
                await send(make_error_frame("start", "empty message"))
                continue

            # 模型白名单：清单外模型直接拒（不透传——DIY/前端的模型边界在服务端强制）
            model = (data.get("model") or "").strip()
            if model:
                from src.runtime.model_client import is_model_allowed, normalize_model_id

                normalized = normalize_model_id(model)
                if normalized is None or not is_model_allowed(normalized):
                    await send(make_error_frame("start", f"model not available: {model}"))
                    continue

            session_id = data.get("session_id") or uuid.uuid4().hex[:16]
            session = _sessions.get(session_id)
            if session is not None and session.user_id and session.user_id != user_id:
                await send(make_error_frame("start", "session owned by another user"))
                continue
            if session is None:
                # 进程重启 / 首次接入：从库重建 LLM 上下文（persisted_count 对齐，
                # 下轮只落新增段）。库无此会话 = 全新会话，首条消息落库时 upsert。
                try:
                    from db.mongodb.repositories import agent_message_repo
                    from db.mysql.repositories import agent_session_repo

                    info = await asyncio.to_thread(agent_session_repo.get_session, session_id)
                    if info is not None and info["user_id"] != user_id:
                        await send(make_error_frame("start", "session owned by another user"))
                        continue
                    items = await agent_message_repo.list_items(session_id)
                except Exception:  # noqa: BLE001 —— 库不可达时按新会话继续（不阻断）
                    items = []
                session = SessionState(
                    session_id=session_id,
                    user_id=user_id,
                    history=items,
                    persisted_count=len(items),
                )
                _sessions[session_id] = session
            # ===== 附件（§6.2 start 帧扩展）=====
            raw_attachments = data.get("attachments") or []
            attachments_meta: list = []
            stubs_block = ""
            image_parts: list = []
            if raw_attachments:
                if not isinstance(raw_attachments, list) or len(raw_attachments) > 20:
                    await send(make_error_frame("start", "invalid attachments"))
                    continue
                fids: list[str] = []
                for a in raw_attachments:
                    if not isinstance(a, dict):
                        continue
                    fid = str(a.get("file_id") or "").strip()
                    if fid and fid not in fids:
                        fids.append(fid)
                if not fids:
                    await send(make_error_frame("start", "invalid attachments"))
                    continue
                ok, prepared = await _prepare_attachments(fids, user_id, session_id, model)
                if not ok:
                    await send(make_error_frame("start", str(prepared)))
                    continue
                attachments_meta, stubs_block, image_parts = prepared

            deps = ToolDeps(
                user_id=user_id,
                auth_token=ws.query_params.get("token", ""),
                agent_id="homepage",
                session_id=session_id,
            )
            current_task = asyncio.create_task(
                _run_turn_guarded(
                    session,
                    message,
                    model,
                    (data.get("thinking_level") or "").strip(),
                    deps,
                    attachments=attachments_meta,
                    stubs_block=stubs_block,
                    image_parts=image_parts,
                )
            )

    except WebSocketDisconnect:
        logger.info(f"ws chat disconnected: user={user_id}")
    finally:
        if current_task and not current_task.done():
            current_task.cancel()
