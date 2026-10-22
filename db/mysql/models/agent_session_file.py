#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : agent_session_file.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    会话工作空间文件目录表（agent_session_file，MySQL）

    设计文档：docs/会话附件与工作空间设计.md §3.1。
    一行 = 工作空间里的一个文件（上传附件或 agent 执行产物），物理内容在
    MinIO 桶 agent-workspace（{user_id}/{session_id}/{file_id} 前缀族）。
    - kind：file（文档/数据）/ image（图片附件）
    - origin：upload / agent（D6 来源标签，差异化清理/配额/展示）
    - session_id 为 NULL = 临时区行（D1 首条消息前上传，start 帧时绑定归位）
    留存/软删复用 BaseModel 的 deleted / update_time，零附加列（D9）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    String,
    Text,
)

from db.mysql.models.base_model import BaseModel


class AgentSessionFile(BaseModel):
    """会话工作空间文件目录表"""

    __tablename__ = "agent_session_file"

    __table_args__ = (
        # 会话内附件列表 / 配额统计（deleted 过滤在查询里带）
        Index("idx_agtfile_session", "session_id", "deleted"),
        # 临时区孤儿回收（session_id IS NULL + create_time，D11）
        Index("idx_agtfile_temp", "session_id", "create_time"),
        # 解析失败行清理
        Index("idx_agtfile_status", "parse_status", "update_time"),
    )

    # ========== 主键 ==========
    id = Column(
        String(64),
        primary_key=True,
        comment="文件ID（uuid hex16；同时是 MinIO 对象键的文件段）",
    )

    # ========== 归属 ==========
    user_id = Column(
        String(64),
        nullable=False,
        comment="所属用户ID（=会话属主，权限校验入口）",
    )
    session_id = Column(
        String(64),
        nullable=True,
        comment="所属会话ID；NULL=临时区（D1 首条消息前上传，绑定后回填）",
    )

    # ========== 分型与来源 ==========
    kind = Column(
        String(16),
        nullable=False,
        comment="文件分型：file=文档/数据（走解析或原始保留）；image=图片附件",
    )
    origin = Column(
        String(16),
        nullable=False,
        default="upload",
        comment="来源：upload=用户上传；agent=执行产物（D6 分级策略标签）",
    )

    # ========== 文件元数据 ==========
    name = Column(String(255), nullable=False, comment="原始文件名（展示用）")
    mime = Column(String(128), nullable=False, default="", comment="MIME 类型（嗅探后判定）")
    size = Column(BigInteger, nullable=False, default=0, comment="字节数")

    # ========== 解析状态（文档类） ==========
    parse_status = Column(
        String(16),
        nullable=False,
        default="pending",
        comment="解析状态：pending/parsing/ready/failed（image 与 raw 类上传即 ready）",
    )
    parse_error = Column(Text, nullable=True, comment="解析失败原因（failed 时可见）")
    md_object_key = Column(
        String(512),
        nullable=True,
        comment="解析产物 markdown 的 MinIO 对象键（无解析产物为 NULL）",
    )
    md_chars = Column(Integer, nullable=False, default=0, comment="解析产物字数（存根摘要用）")
    asset_count = Column(Integer, nullable=False, default=0, comment="解析抽出图片资产数")
