#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : workspace.py（src/tools/builtin）
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    会话工作空间工具面（设计 §4.3 / §5 / D13 / D18）：
    - list_workspace_files：名称 → file_id 权威映射（发现三层之三）
    - read_workspace_file：按需读全文/标题分节/行号窗口（D13 双模式）
    - understand_image：图片按需理解（路线 B；D4 走 [presets.multimodal]，
      两路线通用——多轮后凭存根重新看图也走这里，D12）

    发现三层（D18）：首轮存根一次性注入（ws 路由负责）+ 沙箱 os.listdir
    自查（code_execute 预载）+ 本文件的 list 工具。工具 description 静态。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import asyncio
import base64

from agents import RunContextWrapper, function_tool
from loguru import logger

from src.tools.registry import ToolDeps

# 原始文件里可按文本直读的扩展名（read_workspace_file 的 raw 回退档）
_TEXTY_EXTS = {
    ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".md", ".markdown", ".txt",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
    ".sql", ".sh", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".log",
    ".ini", ".conf",
}


def _derive_route(info: dict) -> str:
    if info.get("kind") == "image":
        return "image"
    if info.get("md_object_key"):
        return "mineru"  # 有解析投影（mineru/direct_md 同形态，展示标签共用）
    return "raw"


async def _owned_in_session(file_id: str, deps: ToolDeps) -> tuple[dict, str] | tuple[str, None]:
    """属主 + 会话校验；返回 (错误文本, None) 或 (info, None-ok)。"""
    from db.mysql.repositories import agent_session_file_repo as file_repo

    info = await asyncio.to_thread(file_repo.get_file, file_id)
    if info is None or info["user_id"] != deps.user_id:
        return f"文件不存在: {file_id}", None
    if deps.session_id and info["session_id"] != deps.session_id:
        return f"文件 {info['name']} 不在当前会话工作空间内", None
    return info, None


@function_tool
async def list_workspace_files(ctx: RunContextWrapper[ToolDeps]) -> str:
    """列出当前会话工作空间的文件清单（含 file_id、名称、类型、大小、解析字数与摘要）。

    用途：拿到 file_id（read_workspace_file / understand_image 的必填参数），
    或在执行产物出现后刷新清单。用户上传的附件与代码执行生成的产物都在其中。
    """
    from db.mysql.repositories import agent_session_file_repo as file_repo
    from src.service.workspace.file_views import build_file_stub

    deps = ctx.context or ToolDeps()
    if not deps.session_id:
        return "当前会话没有工作空间"

    items = await asyncio.to_thread(file_repo.list_by_session, deps.session_id)
    if not items:
        return "当前会话工作空间为空（无附件、无产物）"
    lines = []
    for info in items:
        # 文档类顺手带开头摘要 + 标题目录（§4.3 存根同款；小对象读，只在列表时发生）
        md_head = None
        toc = None
        if info.get("md_object_key") and info.get("md_chars"):
            try:
                from src.service.workspace.file_views import build_md_toc
                from src.service.workspace.upload_service import read_workspace_object

                text = (await read_workspace_object(info, "md")).decode("utf-8", errors="replace")
                md_head = text[:400]
                toc = build_md_toc(text)
            except Exception:  # noqa: BLE001 —— 摘要/目录失败不影响清单
                pass
        lines.append(build_file_stub(dict(info, route=_derive_route(info)), md_head=md_head, toc=toc))
    return "\n".join(lines)


@function_tool
async def read_workspace_file(
    ctx: RunContextWrapper[ToolDeps],
    file_id: str,
    section: str = "",
    start_line: int = 0,
    end_line: int = 0,
) -> str:
    """读取当前会话工作空间里的文件内容（markdown 解析产物优先，数据/代码文件读原文）。

    三种读法：
    1. 全文：不传任何可选参数（超长会截断并提示）；
    2. 按标题分节：传 section（标题的子串；附件存根/list_workspace_files 的「目录」
       里列出的标题可直接用，如 "季度总结"）；
    3. 按行号窗口：传 start_line / end_line（1 起、含端点，长文翻页用）。

    Args:
        file_id: 文件 ID（来自附件存根或 list_workspace_files）。
        section: 可选，markdown 标题子串（见存根「目录」），只读该节。
        start_line: 可选，起始行号（1 起）。
        end_line: 可选，结束行号（含）。
    """
    import os

    from src.service.workspace.file_views import (
        clamp_for_model,
        find_section,
        line_window,
        split_md_sections,
    )
    deps = ctx.context or ToolDeps()
    info, err = await _owned_in_session(file_id, deps)
    if err:
        return err

    from src.service.workspace.upload_service import read_workspace_object

    if info.get("md_object_key"):
        which = "md"
    else:
        ext = os.path.splitext(info["name"])[1].lower()
        if ext not in _TEXTY_EXTS:
            return (
                f"{info['name']} 是二进制/数据文件（{ext or '未知类型'}），不适合整读文本；"
                "请用 code_execute 在沙箱中加载分析（文件已预载进沙箱文件系统）。"
            )
        which = "raw"

    try:
        data = await read_workspace_object(info, which)
    except Exception as e:  # noqa: BLE001
        return f"读取失败: {e}"
    text = data.decode("utf-8", errors="replace")
    total_lines = len(text.splitlines())

    if section.strip():
        picked = find_section(text, section.strip())
        if picked is None:
            titles = "、".join(t for t, _ in split_md_sections(text))[:400]
            return f"未找到标题含「{section}」的小节。可用标题：{titles}"
        body = picked
        mode = f"节「{section.strip()}」"
    elif start_line > 0 or end_line > 0:
        body = line_window(text, start_line or 1, end_line or None)
        mode = f"行 {start_line or 1}-{end_line or total_lines}"
    else:
        body = text
        mode = "全文"

    header = f"【{info['name']}｜{mode}｜全文 {info.get('md_chars') or len(text):,} 字 / {total_lines:,} 行】\n"
    return header + clamp_for_model(body)


@function_tool
async def understand_image(
    ctx: RunContextWrapper[ToolDeps],
    file_id: str,
    question: str = "",
) -> str:
    """用视觉模型理解当前会话工作空间里的一张图片，返回文字描述。

    何时使用：本轮对话的主模型不支持直接看图，或需要针对图片回答具体问题
    （图表数据、图中文字、界面截图内容等）。图片本体由平台保管，无需传图。

    Args:
        file_id: 图片文件 ID（来自附件存根或 list_workspace_files）。
        question: 可选，针对图片的具体问题；空则输出整体描述。
    """
    deps = ctx.context or ToolDeps()
    info, err = await _owned_in_session(file_id, deps)
    if err:
        return err
    if info.get("kind") != "image":
        return f"{info['name']} 不是图片文件（kind={info.get('kind')}）"

    from src.service.workspace.upload_service import read_workspace_object

    try:
        data = await read_workspace_object(info, "raw")
    except Exception as e:  # noqa: BLE001
        return f"图片读取失败: {e}"

    data_url = f"data:{info.get('mime') or 'image/png'};base64,{base64.b64encode(data).decode('ascii')}"
    prompt = question.strip() or "请详细描述这张图片的内容（如果是图表，请读出关键数据）。"
    try:
        answer = await _vlm_describe(data_url, prompt)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[workspace] understand_image 失败: {file_id}, {e}")
        return f"图片理解失败: {e}"
    return f"【{info['name']} 的理解结果】\n{answer}"


async def _vlm_describe(data_url: str, prompt: str) -> str:
    """
    路线 B 的 VLM 调用（D4）：[presets.multimodal] 绑定的模型 + 采样参数。

    模型经 model_client 的网关客户端直连（qwen/qwen3.7-flash）；思考档位走
    registry → thinking_adapter 同一条链（与 build_model_settings 同构，只是
    这里直接产 OpenAI 调用 kwargs 而非 SDK ModelSettings）。
    """
    from src.runtime.model_client import get_openai_client, normalize_model_id
    from src.utils.config_profile import load_profile_presets

    presets = load_profile_presets()
    preset = presets.get("multimodal")
    if not preset or not preset.get("model"):
        raise RuntimeError("未配置 [presets.multimodal]（model profile 的 multimodal 预设）")
    model = normalize_model_id(preset["model"]) or preset["model"]

    kwargs: dict = {}
    if preset.get("temperature") is not None:
        kwargs["temperature"] = float(preset["temperature"])
    if preset.get("max_tokens"):
        kwargs["max_tokens"] = int(preset["max_tokens"])

    thinking_level = str(preset.get("thinking_level") or "").strip()
    if thinking_level:
        try:
            from src.client.llm import get_litellm_registry, get_thinking_adapter

            effort = get_litellm_registry().resolve_reasoning_effort(model, thinking_level)
            if effort:
                adapted = get_thinking_adapter(model).adapt(model, effort)
                if adapted.get("reasoning_effort"):
                    kwargs["reasoning_effort"] = adapted["reasoning_effort"]
                extra = dict(adapted.get("extra_body") or {})
                for k, v in adapted.items():
                    if k not in ("reasoning_effort", "extra_body"):
                        extra[k] = v
                if extra:
                    kwargs["extra_body"] = extra
        except Exception as e:  # noqa: BLE001 —— 思考参数失败不阻断看图
            logger.warning(f"[workspace] VLM 思考档位适配失败（忽略）: {e}")

    client = get_openai_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        timeout=float(preset.get("timeout") or 90),
        **kwargs,
    )
    return (resp.choices[0].message.content or "").strip() or "(视觉模型未返回内容)"
