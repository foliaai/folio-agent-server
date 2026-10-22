#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""user content 展示清洗单测（回放一致性：剥存根块/图片占位，items 不动）"""

from db.mongodb.repositories.agent_message_repo import AgentMessageRepository


def test_plain_text_untouched():
    item = {"role": "user", "content": "普通消息"}
    assert AgentMessageRepository._user_text(item) == "普通消息"


def test_stub_block_stripped():
    item = {
        "role": "user",
        "content": "这个文档说了什么？\n\n[本次会话附件]\n- a.pdf（文档，1.2MB，解析 20 字） 摘要: ... [file_id: abc]",
    }
    assert AgentMessageRepository._user_text(item) == "这个文档说了什么？"


def test_image_placeholder_part_skipped():
    item = {
        "role": "user",
        "content": [
            {"type": "text", "text": "图里是什么？"},
            {"type": "text", "text": "[图片附件 测试.png：file_id=abc，需要时可调 understand_image 重新查看]"},
        ],
    }
    assert AgentMessageRepository._user_text(item) == "图里是什么？"


def test_parts_with_stub_stripped():
    item = {
        "role": "user",
        "content": [
            {"type": "text", "text": "读附件\n\n[本次会话附件]\n- x.txt（文档，10B）"},
        ],
    }
    assert AgentMessageRepository._user_text(item) == "读附件"
