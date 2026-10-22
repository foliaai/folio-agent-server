#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : agent_conversation.py
@Author  : caixiongjiang
@Date    : 2026/10/04
@Function:
    通用 Agent 会话元信息表（agent_session，MySQL）

    与 AKS（folio-aks-server）对话存储的关系：**同构不共表**——MySQL 存
    会话元信息（本文件，对齐 AKS chat_session），MongoDB 存消息正文
    （db/mongodb/models/agent_message.py，对齐 AKS chat_message）。
    表/集合归 agent-server 私有，不与 AKS 共享。

    消息正文（含 SDK 原生 items）在 MongoDB agent_message 集合；MySQL 版
    agent_message 表已于 v1.1.0 迁移并 DROP；LLM 上下文装配见
    db/mongodb/repositories/agent_message_repo.list_items。
@Modify History:
    2026-10-04 消息侧迁 MongoDB（同构不共表），本文件收敛为 session-only

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
)

from db.mysql.models.base_model import BaseModel


class AgentSession(BaseModel):
    """
    通用 Agent 会话元信息表

    字段说明
    --------
    主键
        - ``session_id``: 会话 ID（uuid hex16，REST 建会话时生成）

    归属
        - ``user_id``: 必填，会话所有者；权限校验入口
        - ``title``: 会话标题（首条用户消息截断生成，前端可改）

    模型与策略（轮级偏好，记最近一次使用的值）
        - ``model``: 模型 id（/api/v1/models 清单项；NULL=后端默认）
        - ``thinking_level``: 思考强度档位（off/minimal/low/medium/high/xhigh/max）

    运行计数
        - ``message_count``: 已落库消息行数（user + assistant，Mongo 侧）
        - ``last_message_at``: 最近一条消息时间（会话列表排序键）

    复合索引
        - ``idx_user_lastmsg(user_id, last_message_at)``: 会话列表按活跃度倒序
        - ``idx_user_deleted(user_id, deleted)``: 软删除过滤
    """

    __tablename__ = "agent_session"

    __table_args__ = (
        Index("idx_agtsession_user_lastmsg", "user_id", "last_message_at"),
        Index("idx_agtsession_user_deleted", "user_id", "deleted"),
    )

    # ========== 主键 ==========
    session_id = Column(
        String(64),
        primary_key=True,
        comment="会话ID（uuid hex16）",
    )

    # ========== 归属 ==========
    user_id = Column(
        String(64),
        nullable=False,
        comment="所属用户ID（权限校验入口）",
    )

    title = Column(
        String(255),
        nullable=False,
        default="新会话",
        comment="会话标题（首条用户消息截断生成，前端可改）",
    )

    # ========== 模型与策略（记最近一次使用的值，轮级覆盖不落行） ==========
    model = Column(
        String(255),
        nullable=True,
        comment="最近使用的模型 id（/api/v1/models 清单项；NULL=后端默认）",
    )

    thinking_level = Column(
        String(16),
        nullable=False,
        default="off",
        comment="最近使用的思考强度档位（off/minimal/low/medium/high/xhigh/max）",
    )

    # ========== 运行计数 ==========
    message_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="已落库消息数（user + assistant，Mongo agent_message 集合）",
    )

    last_message_at = Column(
        DateTime,
        nullable=True,
        comment="最近一条消息时间（会话列表按活跃度倒序）",
    )
