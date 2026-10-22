#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : registry.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    工具注册表（架构方案 §3）。
    - ToolDeps：per-request 工具上下文（用户身份/token/agent 标识），
      经 SDK 的 Agent(dependencies=...) + RunContextWrapper 注入
    - build_homepage_tools()：首页 agent 的工具面（每次请求装配，deps 不同）
    - TOOLS_REGISTRY：元数据表（M3 DIY 表单的勾选清单来源）
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolDeps:
    """一次 agent 运行的工具上下文（WS 鉴权后填充）"""

    user_id: str = ""
    auth_token: str = ""      # 透传 AKS（kb_retrieval 需要）
    agent_id: str = "homepage"
    session_id: str = ""


@dataclass
class ToolMeta:
    """工具元数据（注册表条目）"""

    name: str
    description: str
    permission: str = "built-in"  # built-in / user-code / external-api（§3 权限三级）


# 平台工具元数据（M3 DIY 表单数据源；与 builtin/ 实现一一对应）
TOOLS_REGISTRY: List[ToolMeta] = [
    ToolMeta(name="web_search", description="Web 搜索（Tavily），获取公开网络信息"),
    ToolMeta(name="kb_retrieval", description="检索 folio 知识库（只读，跨服务调 AKS）"),
    ToolMeta(name="code_execute", description="在 WASM 沙箱中执行 Python 代码做计算/数据处理"),
    ToolMeta(name="list_workspace_files", description="列出会话工作空间文件清单（名称→file_id 映射）"),
    ToolMeta(name="read_workspace_file", description="读取工作空间文件内容（全文/标题分节/行号窗口）"),
    ToolMeta(name="understand_image", description="用视觉模型按需理解工作空间里的图片"),
]


def build_homepage_tools() -> List[Any]:
    """首页 agent 的工具面（SDK FunctionTool 实例列表）。

    工具实现内部经 RunContextWrapper[ToolDeps] 拿 per-request 上下文，
    因此工具实例可全局复用（deps 在 run 时注入，不烤死在工具里）。
    """
    from src.tools.builtin.code_execute import code_execute
    from src.tools.builtin.kb_retrieval import kb_retrieval
    from src.tools.builtin.web_search import web_search
    from src.tools.builtin.workspace import (
        list_workspace_files,
        read_workspace_file,
        understand_image,
    )

    return [web_search, kb_retrieval, code_execute, list_workspace_files, read_workspace_file, understand_image]
