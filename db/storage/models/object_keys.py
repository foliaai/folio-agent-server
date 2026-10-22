#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : object_keys.py（db/storage/models）
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    工作空间对象键模型（对齐 db/*/models 的"结构定义"职责——对象存储无表，
    键规则即模型）。设计 §3.1 对象键族：

      原始文件   {user_id}/{session_id}/{file_id}
      解析产物   {user_id}/{session_id}/{file_id}.md
      图片资产   {user_id}/{session_id}/{file_id}.assets/{image_name}
      临时区     {user_id}/temp-{file_id}/...（D1，绑定后搬运会话前缀）

    session_id 传 None 时由调用方给 temp-{file_id} 形态（见 upload_service）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""


def build_raw_key(user_id: str, session_id: str, file_id: str) -> str:
    """原始文件对象键"""
    return f"{user_id}/{session_id}/{file_id}"


def build_md_key(user_id: str, session_id: str, file_id: str) -> str:
    """解析产物 markdown 对象键"""
    return f"{user_id}/{session_id}/{file_id}.md"


def build_asset_key(user_id: str, session_id: str, file_id: str, image_name: str) -> str:
    """MinerU 抽出图片资产对象键（名称去路径分隔符防穿越）"""
    safe = image_name.replace("/", "_").replace("\\", "_")
    return f"{user_id}/{session_id}/{file_id}.assets/{safe}"


def build_file_prefix(user_id: str, session_id: str, file_id: str) -> str:
    """一个文件的全部对象前缀（raw + md + assets），清理时整前缀删除"""
    return f"{user_id}/{session_id}/{file_id}"


def build_temp_prefix(user_id: str, file_id: str) -> str:
    """临时区对象前缀（D1：上传于会话创建前，绑定后由 upload_service 搬运）"""
    return f"{user_id}/temp-{file_id}"


def temp_session_id(file_id: str) -> str:
    """临时区形态的 session 段（与 build_raw_key 等组合使用）"""
    return f"temp-{file_id}"
