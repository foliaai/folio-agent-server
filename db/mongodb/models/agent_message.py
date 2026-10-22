#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : agent_message.py
@Author  : caixiongjiang
@Date    : 2026/10/04
@Function:
    AgentMessage Document 定义（通用 Agent 会话消息，MongoDB）

    与 AKS ChatMessage 的关系：**同构不共集合**。字段结构对齐
    （BaseDocument 审计字段 / 软删 / (session_id, create_time) 索引 /
    正序回放），集合归 agent-server 私有。

    行粒度：一轮 turn = user + assistant 两行（MySQL agent_session 存会话
    元信息，本集合存消息正文——与 AKS 的 MySQL+Mongo 双库分工一致）。

    与 MySQL 版 agent_message（已下线）的差异：items 列保留（SDK 原生
    input items 数组，LLM 上下文零转换装配），无 tool_call_id/citations 等
    AKS 专属字段。
@Modify History:
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from db.mongodb.models.base_model import BaseDocument


from pydantic import BaseModel as PydanticBaseModel


class ToolCallEntry(PydanticBaseModel):
    """单次工具调用记录（UI 回放用；LLM 侧全文在 items 的 function_call_output）"""

    id: str = Field(..., description="调用 ID")
    name: str = Field(..., description="工具名")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="参数（已解析）")
    result_brief: Optional[str] = Field(None, description="结果摘要（前几百字）")
    time_ms: Optional[float] = Field(None, description="耗时（毫秒）")

    model_config = ConfigDict(extra="ignore")


class UsageRecord(PydanticBaseModel):
    """本轮 token 用量"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # 前缀缓存命中数（model_lake/litellm 均透传 prompt_tokens_details.cached_tokens；
    # 命中率 = cached/input，session 级按 token 加权平均）
    cached_tokens: int = 0

    model_config = ConfigDict(extra="ignore")


class AgentMessage(BaseDocument):
    """
    通用 Agent 会话消息文档

    角色与字段约定
    --------------
    - ``role=user``      : ``content`` 为用户输入；``items``=[user item]
    - ``role=assistant`` : ``content``=最终正文；``thinking``/``thinking_ms``；
                           ``tool_calls``=本轮全部工具记录；``usage``；
                           ``items``=本轮 LLM 侧全部原生 input items
    - ``role=summary``   : M3 上下文压缩预留

    主键：``agentmsg_<纳秒时间戳>_<rand>``——时间前缀保证同秒写入的
    user/assistant 两行字典序即时间序（回放与装配按 create_time, _id 排序）。
    """

    # ========== 主键 ==========
    id: str = Field(
        ...,
        alias="_id",
        description="消息唯一标识（agentmsg_<ns 时间戳>_<rand>）",
    )

    # ========== 关联 ==========
    session_id: str = Field(..., max_length=64, description="所属会话 ID")
    user_id: str = Field(..., max_length=64, description="所属用户 ID（冗余，审计用）")

    # ========== 角色与正文 ==========
    role: Literal["user", "assistant", "summary"] = Field(..., description="角色")
    content: str = Field(default="", description="消息正文")

    # ========== assistant 专属 ==========
    thinking: Optional[str] = Field(None, description="思考链全文")
    thinking_ms: Optional[float] = Field(None, description="思考墙钟耗时（毫秒）")
    tool_calls: List[ToolCallEntry] = Field(default_factory=list, description="工具调用记录")
    usage: Optional[UsageRecord] = Field(None, description="本轮 token 用量")
    finish_reason: Optional[str] = Field(None, description="结束原因（stop/length/error 等）")

    # ========== 附件（user 行专属；D16 存 file_id+存根快照，回放零查库） ==========
    attachments: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="本轮附件元数据 [{file_id, kind, name, size, mime}]（空=无附件轮）",
    )

    # ========== LLM 上下文（唯一装配来源） ==========
    items: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="该行代表的 SDK 原生 input items 数组",
    )

    # ========== Pydantic 配置 ==========
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    # ========== Beanie 配置 ==========
    class Settings:
        name = "agent_message"
        use_state_management = True
        validate_on_save = True
        indexes = [
            # 高频：拉某会话历史（按时间正序，回放 + LLM 装配）
            IndexModel(
                [("session_id", ASCENDING), ("create_time", ASCENDING), ("_id", ASCENDING)],
                name="idx_session_create_time",
            ),
            # 审计：用户最近消息（倒序）
            IndexModel(
                [("user_id", ASCENDING), ("create_time", DESCENDING)],
                name="idx_user_create_time",
            ),
            # 软删除过滤
            IndexModel(
                [("deleted", ASCENDING), ("create_time", DESCENDING)],
                name="idx_deleted_create_time",
            ),
        ]
