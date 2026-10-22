#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : agent_message_repo.py
@Author  : caixiongjiang
@Date    : 2026/10/04
@Function:
    AgentMessage Repository（MongoDB，对齐 AKS chat_message_repo 的访问面）

    - ``insert_turn``     : 一轮两行（user + assistant）原子写入
    - ``list_by_session`` : 回放（按 create_time/_id 正序分页）
    - ``list_items``      : LLM 上下文装配（正序拉行展开 items）
    - ``count_by_session``: 会话内活跃消息数
    - ``soft_delete_by_session``: 清空消息（软删，会话保留在 MySQL 侧）
@Modify History:
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from db.mongodb.models.agent_message import AgentMessage, ToolCallEntry, UsageRecord
from db.mongodb.repositories.base_repository import BaseRepository


def _message_id() -> str:
    # 纳秒时间前缀：同一秒内写入的多行按 _id 字典序即时间序
    return f"agentmsg_{int(time.time() * 1e9):019d}_{uuid.uuid4().hex[:8]}"


class AgentMessageRepository(BaseRepository[AgentMessage]):
    """AgentMessage Repository（异步）"""

    def __init__(self) -> None:
        super().__init__(AgentMessage)

    # ==================== 写入 ====================

    @staticmethod
    def _user_text(user_item: Dict[str, Any]) -> str:
        """
        user item content → 展示用纯文本（发送时用户输入的原文）。

        清洗两类注入文本（LLM 上下文专用，不该给用户看，回放一致性）：
        - 存根块：message 后拼接的 ``\n\n[本次会话附件]...``（§4.3 首轮注入）；
        - 图片占位：D12 净化替换出的 ``[图片附件 ...]`` text part。
        注意 items 原样保留（LLM 装配依赖存根），只清洗 content 展示字段。
        """
        content = user_item.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and not str(part.get("text", "")).startswith("[图片附件")
            )
        else:
            text = str(content or "")
        marker = text.find("[本次会话附件]")
        if marker >= 0:
            text = text[:marker]
        return text.strip()

    async def insert_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        user_item: Dict[str, Any],
        assistant_items: List[Dict[str, Any]],
        assistant_content: str,
        thinking: Optional[str] = None,
        thinking_ms: Optional[float] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        usage: Optional[Dict[str, Any]] = None,
        finish_reason: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        落一轮对话（user + assistant 两行，同时间戳写入保持 _id 有序）。

        Returns:
            写入条数（正常为 2）
        """
        try:
            docs = [
                AgentMessage(
                    _id=_message_id(),
                    session_id=session_id,
                    user_id=user_id,
                    role="user",
                    content=self._user_text(user_item),
                    items=[user_item],
                    attachments=attachments or [],
                    creator=user_id,
                    updater=user_id,
                ),
                AgentMessage(
                    _id=_message_id(),
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_content,
                    thinking=thinking,
                    thinking_ms=thinking_ms,
                    tool_calls=[ToolCallEntry(**tc) for tc in (tool_calls or [])],
                    usage=UsageRecord(**usage) if usage else None,
                    finish_reason=finish_reason,
                    items=assistant_items,
                    creator=user_id,
                    updater=user_id,
                ),
            ]
            await AgentMessage.insert_many(docs)
            return len(docs)
        except Exception as e:  # noqa: BLE001
            logger.error(f"落库 turn 失败: session={session_id}, err={e}", exc_info=True)
            raise

    # ==================== 读取 ====================

    async def list_by_session(
        self,
        session_id: str,
        *,
        limit: int = 50,
        skip: int = 0,
        ascending: bool = True,
        include_deleted: bool = False,
    ) -> List[AgentMessage]:
        """回放：按 create_time/_id 正序分页（user 行在前，轮内顺序稳定）"""
        try:
            sort_dir = 1 if ascending else -1
            return await self.find(
                limit=limit,
                skip=skip,
                include_deleted=include_deleted,
                sort=[("create_time", sort_dir), ("_id", sort_dir)],
                session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"拉取会话历史失败: {e}", exc_info=True)
            return []

    async def list_items(self, session_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        """
        LLM 上下文装配：按时间正序拉行展开 items 拼接（零转换）。
        以行为原子单位截断，永不破坏 function_call 与其 output 的配对。
        """
        rows = await self.list_by_session(session_id, limit=limit)
        items: List[Dict[str, Any]] = []
        for r in rows:
            items.extend(r.items or [])
        return items

    # ==================== 计数 / 删除 ====================

    async def count_by_session(self, session_id: str) -> int:
        return await self.count(session_id=session_id)

    async def soft_delete_by_session(self, session_id: str, *, updater: str = "") -> int:
        """软删会话内全部活跃消息（清空消息，会话保留）"""
        try:
            result = await self.model.find(
                {"session_id": session_id, "deleted": 0}
            ).update(
                {
                    "$set": {
                        "deleted": 1,
                        "updater": updater,
                        "update_time": datetime.now(),
                    }
                }
            )
            modified = result.modified_count if result else 0
            logger.debug(f"软删除会话 {session_id} 下的消息 {modified} 条")
            return modified
        except Exception as e:  # noqa: BLE001
            logger.error(f"批量软删除会话消息失败: {e}", exc_info=True)
            return 0


# 全局实例
agent_message_repo = AgentMessageRepository()
