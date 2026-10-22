#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : _env_file_flag.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    从 .env 文件直读开关值（不经 os.environ / env_manager）。

    为什么存在：dotenv 的 load_dotenv(override=True) 只添加/覆盖键、
    从不删除；uvicorn reload 是同进程重导模块——曾在 .env 里后又被删掉的
    键会永久残留 in os.environ，导致"删行不生效、须重启进程"。用于
    DEV_AUTH_BYPASS 这类需要即时切换的本地联调开关（生产硬拒绝的键，
    无需支持 shell 环境变量注入形态，文件即唯一事实源）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from pathlib import Path


def env_file_flag(key: str) -> str:
    """读 .env 中 key 的值（无该键/文件不可读返回空串；不抛异常）"""
    try:
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""
