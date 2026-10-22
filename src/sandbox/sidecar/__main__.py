#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : __main__.py（sidecar 入口）
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    python -m src.sandbox.sidecar
    启动即向 stdout 打 ready 帧（宣告可用引擎），随后进入协议主循环。
    部署要求：以低权限 OS 用户运行（无 home、无 shell、只读二进制）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import sys

from src.sandbox.sidecar import engines, server


def main() -> None:
    ready = {"type": "ready", "engines": engines.available_engines()}
    sys.stdout.write(_json_line(ready))
    sys.stdout.flush()
    server.run()


def _json_line(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    main()
