#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : file_types.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    上传文件分型路由 + 内容嗅探（设计 §4.1 / D10 / D19）。

    纯函数模块（无 IO），单测在 test/workspace/test_file_types.py。
    分型结果决定解析路由：
      image     → 压缩存储，parse_status 直接 ready
      mineru    → PDF/docx/pptx，后台 MinerU → markdown（分钟级）
      direct_md → md/txt，直接存为解析产物
      raw       → CSV/JSON/xlsx/代码等，仅存原始文件（沙箱/read 工具直读）
    可执行文件（MZ/ELF/Mach-O 魔数或扩展名黑名单）直接拒绝上传（D10）。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import os
from typing import Optional, Tuple

# ---------- 扩展名分型表 ----------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MINERU_EXTS = {".pdf", ".docx", ".pptx"}
DIRECT_MD_EXTS = {".md", ".markdown", ".txt"}
# xlsx 明确归数据档（D19）；xls 老格式沙箱 pandas 可读，同样直存
RAW_DATA_EXTS = {
    ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".xlsx", ".xls",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
    ".sql", ".sh", ".yaml", ".yml", ".toml", ".xml", ".html", ".css",
    ".log", ".ini", ".conf",
}

# 可执行/脚本危险扩展名黑名单（内容嗅探之外的第二道闸，D10）
EXECUTABLE_EXTS = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".bat", ".cmd", ".com",
    ".msi", ".app", ".apk", ".jar", ".class", ".deb", ".rpm", ".scr", ".vbs",
}

# 魔数表（嗅探只做"足够区分路由"的判定，不做全量 libmagic）
_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png", "image"),
    (b"\xff\xd8\xff", "image/jpeg", "image"),
    (b"GIF87a", "image/gif", "image"),
    (b"GIF89a", "image/gif", "image"),
    (b"RIFF", None, None),  # webp/wav 需看第 8 字节，特判
    (b"%PDF-", "application/pdf", "mineru"),
    (b"PK\x03\x04", None, None),  # zip 族（docx/xlsx/pptx），扩展名定路由
]

# RIFF 子类（head 前 12 字节）
_RIFF_WEBP = b"WEBP"


class FileRejectError(Exception):
    """上传被拒（可执行文件等，D10）——HTTP 400"""


def _ext(name: str) -> str:
    return os.path.splitext(name)[0:2][1].lower() if "." in name else ""


def sniff(head: bytes, name: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    内容嗅探：返回 (判定依据描述, mime, 类别)。
    类别：image / mineru / None（未知，回退扩展名路由）。
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "magic:png", "image/png", "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "magic:jpeg", "image/jpeg", "image"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "magic:gif", "image/gif", "image"
    if head[:4] == b"RIFF" and head[8:12] == _RIFF_WEBP:
        return "magic:webp", "image/webp", "image"
    if head.startswith(b"BM") and _ext(name) == ".bmp":
        return "magic:bmp", "image/bmp", "image"
    if head.startswith(b"%PDF-"):
        return "magic:pdf", "application/pdf", "mineru"
    if head[:4] == b"PK\x03\x04":
        return "magic:zip", None, None  # docx/xlsx/pptx 同为 zip，扩展名定
    return "", None, None


def is_executable(head: bytes, name: str) -> bool:
    """可执行判定：魔数（MZ/ELF/Mach-O）或扩展名黑名单（D10 双闸）"""
    if head.startswith(b"MZ"):  # Windows PE（exe/dll）
        return True
    if head[:4] == b"\x7fELF":  # Linux ELF
        return True
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return True  # Mach-O
    if head[:4] == b"\xca\xfe\xba\xbe":
        return True  # Java class / Mach-O universal
    return _ext(name) in EXECUTABLE_EXTS


def route_file(head: bytes, name: str, declared_mime: str = "") -> Tuple[str, str, str]:
    """
    上传分型路由。

    Args:
        head: 文件头字节（前 64 字节足够本表判定）
        name: 原始文件名
        declared_mime: 客户端声明的 MIME（仅参考，不信任）

    Returns:
        (kind, route, mime)：
        kind  = "file" | "image"
        route = "image" | "mineru" | "direct_md" | "raw"
        mime  = 嗅探优先，其次声明值，最后扩展名猜测

    Raises:
        FileRejectError: 可执行文件（D10）
    """
    if is_executable(head, name):
        raise FileRejectError(f"不允许上传可执行文件: {name}")

    basis, mime, category = sniff(head, name)
    ext = _ext(name)

    # 内容说是图片 → 图片（即便扩展名伪装）
    if category == "image":
        return "image", "image", mime or "application/octet-stream"
    if category == "mineru":
        return "file", "mineru", mime or "application/pdf"

    # 扩展名路由（zip 族与未知内容都走这里）
    if ext in IMAGE_EXTS:
        # 扩展名声称图片但内容不是 → 拒（防伪装绕过图片压缩/多模态链路）
        raise FileRejectError(f"文件内容与图片扩展名不符: {name}")
    if ext in MINERU_EXTS:
        return "file", "mineru", "application/pdf" if ext == ".pdf" else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if ext == ".docx"
            else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
    if ext in DIRECT_MD_EXTS:
        return "file", "direct_md", "text/markdown" if ext in (".md", ".markdown") else "text/plain"
    if ext in RAW_DATA_EXTS:
        return "file", "raw", declared_mime or "application/octet-stream"

    # 未知类型：按原始文件保存（kind=file / raw）——工具按需读，不给解析
    return "file", "raw", declared_mime or "application/octet-stream"
