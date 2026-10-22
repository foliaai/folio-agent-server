#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""缓存前缀稳定性回归：跨轮请求的 messages 前缀必须逐字节一致

背景：上游（DeepSeek/Qwen 系）普遍支持自动前缀缓存，命中前提是历史前缀
逐轮稳定。本测试锁住三个事实源：存根文本随 items 重放不变形、SDK 对
item 的序列化确定、_build_user_content 的组装稳定。SDK 升级若改变
序列化形态（前缀失稳 → 缓存全失效），此测试先行报警。
"""

import json
from typing import Any, Dict, List

import httpx
import pytest
from openai import AsyncOpenAI

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner

from src.runtime.agent_loop import _build_user_content


class _Capture:
    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    async def hook(self, request: httpx.Request) -> None:
        if "chat/completions" in str(request.url):
            self.payloads.append(json.loads(request.content.decode("utf-8")))


async def _capture_once(capture: _Capture, input_items: list) -> None:
    client = AsyncOpenAI(
        base_url="http://127.0.0.1:9999/v1",
        api_key="sk-noop",
        http_client=httpx.AsyncClient(event_hooks={"request": [capture.hook]}),
    )
    agent = Agent(
        name="folio-homepage",
        instructions="固定系统提示词",
        model=OpenAIChatCompletionsModel(model="deepseek/deepseek-v4.1-flash", openai_client=client),
        model_settings=ModelSettings(),
    )
    with pytest.raises(Exception):  # noqa: B017 —— 假地址连接失败，请求体已捕获
        await Runner.run(agent, input_items)


@pytest.mark.asyncio
async def test_prefix_identical_across_turns() -> None:
    stubs = "[本次会话附件]\n- 报告.pdf（文档，1.2MB，解析 20,289 字） 摘要: 第一章… [file_id: ab12cd34]"
    capture = _Capture()

    # 轮 1：真实发送形态（SDK 连接失败会重试，取该轮最后一次请求为准）
    user1 = {"role": "user", "content": _build_user_content("这个文档说了什么？", stubs, None)}
    before = len(capture.payloads)
    await _capture_once(capture, [user1])
    p1 = capture.payloads[-1]
    assert len(capture.payloads) > before

    # 轮 2：轮 1 item 原样重放（= items 落库→list_items 装配路径）+ 助手 + 新问题
    history = [dict(user1), {"role": "assistant", "content": "第一章讲…"}]
    user2 = {"role": "user", "content": _build_user_content("第二章呢？", "", None)}
    await _capture_once(capture, history + [user2])
    p2 = capture.payloads[-1]

    m1 = p1["messages"]
    m2 = p2["messages"]
    assert [m["role"] for m in m2][: len(m1)] == [m["role"] for m in m1]

    for i, (a, b) in enumerate(zip(m1, m2)):
        assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False), (
            f"前缀第 {i} 条跨轮不一致——上游前缀缓存将整体失效，检查 SDK 序列化或 items 重放是否变形"
        )
