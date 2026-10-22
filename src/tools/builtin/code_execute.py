#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : code_execute.py
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    code_execute 工具：M1 WASM 沙箱的业务入口（架构方案 §2：
    本文件是沙箱唯一业务调用方）。
    M2 仅开放 Python（js 路径需要 wasm 编译链接入，属 M3 DIY 范畴）。
    结果约定：stdout + result 变量拼装返回给模型。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from agents import RunContextWrapper, function_tool
from loguru import logger

from src.sandbox.contracts import SandboxLang
from src.tools.registry import ToolDeps


def _format_result(r) -> str:
    """SandboxResult → 给模型看的可读文本"""
    if r.ok:
        parts = []
        if r.result:
            parts.append(f"result: {r.result}")
        if r.stdout:
            parts.append(f"stdout:\n{r.stdout}")
        if r.error_code and r.error_code.value == "output_truncated":
            parts.append(f"（注意：{r.error_detail}）")
        return "\n".join(parts) or "(no output)"

    # 失败：错误码 + 精简诊断（stderr 截断，避免把 trace 全喂给模型）
    stderr_tail = (r.stderr or "").strip().splitlines()[-5:]
    diag = "\n".join(stderr_tail)[:1500]
    hint = {
        "fuel_exhausted": "计算量超限（fuel 耗尽），请简化算法",
        "timeout": "执行超时，请减少计算量或优化",
        "oom": "内存超限，请减少数据规模",
        "bad_code": "代码有误（语法/导入不支持）",
        "denied_net": "网络请求被拒（仅允许经 allowlist 的 HTTPS）",
        "denied_fs": "文件系统访问被拒（仅内存 FS）",
    }.get(r.error_code.value if r.error_code else "", "")
    parts = [f"execution failed: {r.error_code.value if r.error_code else 'unknown'}"]
    if hint:
        parts.append(hint)
    if diag:
        parts.append(f"stderr（尾部）:\n{diag}")
    return "\n".join(parts)


@function_tool
async def code_execute(
    ctx: RunContextWrapper[ToolDeps],
    code: str,
    stdin: str = "",
) -> str:
    """在隔离沙箱中执行 Python 代码（用于计算、数据处理、格式转换等确定性任务）。

    环境说明：纯 Python + 常见科学计算库（numpy/pandas 等）不可用则回退纯 Python；
    无网络、无文件系统（除入参）；单次执行限时约 10 秒、内存 64MB。
    当前会话工作空间的文件（用户上传的附件等）已预载进沙箱文件系统，可直接
    `os.listdir()` 查看清单、`open('文件名')` 读取；结果放 `result` 变量（字符串），
    过程输出用 print。

    Args:
        code: Python 源码（完整可执行脚本；结果赋给 result 变量）。
        stdin: 可选，作为输入数据传给脚本的字符串。
    """
    from src.sandbox.client import get_sandbox_client
    from src.tools.builtin.workspace_preload import build_preload_files

    deps = ctx.context or ToolDeps()
    try:
        client = get_sandbox_client()
    except RuntimeError:
        return "code_execute 不可用：沙箱未启用或未初始化"

    preload = await build_preload_files(deps)

    r = await client.execute(
        lang=SandboxLang.PYTHON,
        code=code,
        input=stdin,
        files=preload.files,
        meta={"agent_id": deps.agent_id, "user_id": deps.user_id, "tool": "code_execute"},
    )
    logger.bind(name="sandbox").info(
        f"code_execute via tool: ok={r.ok} err={r.error_code} wall={r.wall_ms}ms "
        f"preloaded={len(preload.files)}{f'({preload.skipped_note})' if preload.skipped_note else ''}"
    )
    note = f"\n（注意：{preload.skipped_note}）" if preload.skipped_note else ""
    return _format_result(r) + note
