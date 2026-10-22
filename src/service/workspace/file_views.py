#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : file_views.py
@Author  : caixiongjiang
@Date    : 2026/10/08
@Function:
    工作空间文件的"读视图"纯函数集（设计 §4.3 / §4.4 / D13 / D14）：
    - build_file_stub：存根（首轮注入 + 列表工具共用形态）
    - rewrite_md_image_links：MinerU md 相对引用 → assets/（D14）
    - split_md_sections：按 markdown 标题分节（D13 模式一）
    - line_window：按行号窗口翻页（D13 模式二）
    全部无 IO，单测在 test/workspace/test_file_views.py。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import re
from typing import Dict, List, Optional, Tuple

# 存根摘要取解析产物开头的字数（§4.3"前几百字"）
STUB_HEAD_CHARS = 240
# 存根目录：标题数 / 总字符上限（防长目录把首轮注入撑爆）
STUB_TOC_MAX_TITLES = 24
STUB_TOC_MAX_CHARS = 700
# read_workspace_file 全文/分节返回的字符上限（防单工具调用打爆上下文）
READ_MAX_CHARS = 24_000

_KIND_LABEL = {"file": "文件", "image": "图片"}
# mineru/direct_md 在目录行里不可分（只看有无 md_object_key），统一"文档"
_ROUTE_LABEL = {
    "mineru": "文档",
    "direct_md": "文档",
    "raw": "数据/代码文件",
    "image": "图片",
}


def _human_size(size: int) -> str:
    v = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.1f}{unit}" if unit != "B" else f"{int(v)}B"
        v /= 1024
    return f"{v:.1f}GB"


def build_file_stub(info: Dict, md_head: Optional[str] = None, toc: Optional[str] = None) -> str:
    """
    目录行 → 存根文本（进首轮消息 / list_workspace_files 输出）。

    形态：`- 季度报告.pdf（PDF/Office 文档，2.1MB，解析 12,345 字，摘要: …）[file_id: ab12cd34]`
    带目录时（md_head 同源产物）：存根行下缩进附 `目录:` 树（# 一级、## 二级缩进），
    目录标题可直接作为 read_workspace_file 的 section 参数（与 split_md_sections 同口径）。
    图片附件：`- 架构图.png（图片，340KB）[file_id: ab12cd34]`（图片本体经 image part
    或 understand_image 查看，摘要/目录无意义）。
    """
    name = info.get("name") or "未命名"
    kind = info.get("kind") or "file"
    route = info.get("route") or ""
    size = int(info.get("size") or 0)
    file_id = info.get("file_id") or info.get("id") or ""

    label = _KIND_LABEL.get(kind, kind)
    route_label = _ROUTE_LABEL.get(route, "")
    type_part = route_label or label

    parts = [f"- {name}（{type_part}，{_human_size(size)}"]
    if kind == "file" and info.get("md_chars"):
        parts.append(f"，解析 {info['md_chars']:,} 字")
    stub = "".join(parts) + "）"

    if md_head:
        head = " ".join(str(md_head).split())
        if head:
            stub += f" 摘要: {head[:STUB_HEAD_CHARS]}"
    if file_id:
        stub += f" [file_id: {file_id}]"
    if toc and toc.strip():
        stub += "\n  目录（read_workspace_file 的 section 可用标题）:\n"
        stub += "\n".join("    " + ln for ln in toc.splitlines())
    return stub


def build_md_toc(
    md: str,
    max_titles: int = STUB_TOC_MAX_TITLES,
    max_chars: int = STUB_TOC_MAX_CHARS,
) -> str:
    """
    解析产物 → 标题目录树（供存根展示；模型据此第一跳即可用 section 精准取节）。

    口径与 split_md_sections 一致（# / ## 两级为节边界），标题可原样喂给
    read_workspace_file 的 section（子串匹配）。## 相对 # 缩进一级。
    超过 max_titles / max_chars 截断并标注总标题数；无标题返回空串。
    """
    entries: List[Tuple[int, str]] = []
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 2:
            title = m.group(2).strip()
            if title:
                entries.append((len(m.group(1)), title))
    if not entries:
        return ""

    lines_: List[str] = []
    used = 0
    for lvl, title in entries:
        item = "  " * (lvl - 1) + " ".join(title.split())
        if len(lines_) >= max_titles or used + len(item) + 1 > max_chars:
            lines_.append(f"…（共 {len(entries)} 个标题，已截断）")
            break
        lines_.append(item)
        used += len(item) + 1
    return "\n".join(lines_)


def build_attachments_block(stubs: List[str]) -> str:
    """存根列表 → 首轮注入的消息块（§4.3；只随绑定附件注入一次，D18）"""
    if not stubs:
        return ""
    return "[本次会话附件]\n" + "\n".join(stubs)


# ---------- D14：MinerU md 图片相对引用重写 ----------

# MinerU md 里图片引用形如 `![](images/xxx.jpg)`（html img 也可能出现）；
# 重写后为 `assets/xxx.jpg`——extract 需兼容两种前缀（存储态取资产名用）
_MD_LINK_RE = re.compile(r"\]\((?:images|assets)/([^)/\s]+)\)")
_HTML_IMG_RE = re.compile(r'src=["\'](?:images|assets)/([^"\')/]+)["\']')


def rewrite_md_image_links(md: str) -> str:
    """`images/xxx.jpg` → `assets/xxx.jpg`（对象键 `.assets/` 的相对引用形态，D14）"""
    md = _MD_LINK_RE.sub(lambda m: f"](assets/{m.group(1)})", md)
    md = _HTML_IMG_RE.sub(lambda m: f'src="assets/{m.group(1)}"', md)
    return md


def extract_asset_names(md: str) -> List[str]:
    """从 md 里反查资产文件名（asset-urls 端点默认全量 presign 用）"""
    names = list(dict.fromkeys(_MD_LINK_RE.findall(md)))
    for name in _HTML_IMG_RE.findall(md):
        if name not in names:
            names.append(name)
    return names


# ---------- 从 content_list 重建解析文本（唯一解析文本装配路径） ----------
#
# 现成 md 是"平文本拼装"，题注/脚注/标题层级等语义只存在于 content_list
# （实测形态：text 项带 text_level 1-6 标题层级；table 项带 table_body +
# table_caption/table_footnote；image 项带 image_caption/image_footnote——
# 注意本实例字段名是 image_* 而非文档口径的 img_*；equation 项 text 自带
# $$..$$ LaTeX）。rebuild_md_from_content_list 按阅读顺序重拼，语义元素
# 直接以标签输出，不经文本匹配。
#
# 表格单元格里的公式是 `$..$` 内联 LaTeX（HTML 块内的 $ 不经过 markdown 层
# 的 remark-math，会原样显示）——latex_to_math_html 把 HTML 片段里的 $..$ /
# $$..$$ 转成 rehype-katex 识别的 <code class="language-math math-inline">。

# 启发式：短文本以「表 N / 图 N / Table N / Figure N」开头视为题注（MinerU
# 对题注的归类不稳，时而落成普通 text 项）
_CAPTION_LINE_RE = re.compile(
    r"^(?:表|图|表格|Table|Figure|Fig\.)\s*\d+[\s　:：.、\-—]", re.IGNORECASE
)
# 启发式：「注：…」短文本视为脚注
_FOOTNOTE_LINE_RE = re.compile(r"^(?:注|注解|备注)[:：]")
# 超过该长度的文本不按启发式归类（避免误伤正文长句）
_HEURISTIC_MAX_LEN = 80

# 显示公式可跨行（MinerU 的 $$..$$ 内容常带换行）；仍排除 <>（MinerU 对
# 单元格做了 HTML 转义，裸 < 会先破坏 HTML 解析）
_LATEX_DISPLAY_RE = re.compile(r"\$\$([^$<>]+?)\$\$")
_LATEX_INLINE_RE = re.compile(r"\$([^$<>\n]+?)\$")


def latex_to_math_html(html: str) -> str:
    """HTML 片段内的 $$..$$ / $..$ LaTeX → rehype-katex 的 math code 元素"""
    html = _LATEX_DISPLAY_RE.sub(
        lambda m: f'<code class="language-math math-display">{m.group(1).strip()}</code>',
        html,
    )
    html = _LATEX_INLINE_RE.sub(
        lambda m: f'<code class="language-math math-inline">{m.group(1).strip()}</code>',
        html,
    )
    return html


def _md_caption(text: str) -> str:
    return f'<p class="md-caption">{latex_to_math_html(str(text).strip())}</p>'


def _md_footnote(text: str) -> str:
    return f'<p class="md-footnote">{latex_to_math_html(str(text).strip())}</p>'


def rebuild_md_from_content_list(content_list: Optional[List[Dict]]) -> str:
    """
    content_list → markdown（唯一装配路径）：
    - text: text_level 1-6 → # 标题；短行命中「表/图 N」「注：」启发式 → 题注/脚注
    - table: 题注 → table_body（内嵌公式转 math 元素）→ 脚注
    - image: ![](img_path) → 题注/脚注（image_*/img_* 两种字段名兼容）
    - equation/未知: text/content 直传（markdown 层 remark-math 处理 $$..$$）
    """
    parts: List[str] = []
    for item in content_list or []:
        itype = item.get("type")
        if itype == "text":
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            level = str(item.get("text_level") or "")
            if level in {"1", "2", "3", "4", "5", "6"}:
                parts.append(f"{'#' * int(level)} {text}")
            elif len(text) <= _HEURISTIC_MAX_LEN and _CAPTION_LINE_RE.match(text):
                parts.append(_md_caption(text))
            elif len(text) <= _HEURISTIC_MAX_LEN and _FOOTNOTE_LINE_RE.match(text):
                parts.append(_md_footnote(text))
            else:
                parts.append(text)
        elif itype == "table":
            for cap in item.get("table_caption") or []:
                if str(cap).strip():
                    parts.append(_md_caption(cap))
            body = str(item.get("table_body") or "").strip()
            if body:
                parts.append(latex_to_math_html(body))
            for fn in item.get("table_footnote") or []:
                if str(fn).strip():
                    parts.append(_md_footnote(fn))
        elif itype == "image":
            img_path = str(item.get("img_path") or "").strip()
            if img_path:
                parts.append(f"![]({img_path})")
            # 字段名兼容两种口径（见模块注释）
            for cap in item.get("image_caption") or item.get("img_caption") or []:
                if str(cap).strip():
                    parts.append(_md_caption(cap))
            for fn in item.get("image_footnote") or item.get("img_footnote") or []:
                if str(fn).strip():
                    parts.append(_md_footnote(fn))
        else:
            # equation 的 text 自带 $$..$$；未知类型兜底 text/content
            text = str(item.get("text") or item.get("content") or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(p for p in parts if p)


# ---------- D13：read_workspace_file 分节 ----------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def split_md_sections(md: str, max_level: int = 2) -> List[Tuple[str, str]]:
    """
    按 markdown 标题切分（默认 # / ## 两级为节边界）。

    Returns:
        [(节标题, 节正文含标题行), ...]；标题前的引导段落归入 "(开头)" 节。
    """
    sections: List[Tuple[str, List[str]]] = []
    current_title = "(开头)"
    current: List[str] = []

    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= max_level:
            if current:
                sections.append((current_title, current))
            current_title = m.group(2).strip()
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((current_title, current))

    return [(title, "\n".join(body)) for title, body in sections]


def find_section(md: str, section: str) -> Optional[str]:
    """按标题（子串、不区分大小写）取节正文；无匹配返回 None。"""
    want = section.strip().lower()
    for title, body in split_md_sections(md):
        if want in title.lower():
            return body
    return None


def line_window(md: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
    """
    行号窗口（D13 模式二）：1-based、含端点；start_line 从 1 起，负数或 0 归 1。
    """
    lines = md.splitlines()
    total = len(lines)
    s = max(1, int(start_line))
    e = min(total, int(end_line)) if end_line is not None else total
    if e < s:
        return ""
    return "\n".join(lines[s - 1 : e])


def clamp_for_model(text: str, max_chars: int = READ_MAX_CHARS) -> str:
    """截断给模型的文本，带可读提示（后续可翻页再取）。"""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n…（已截断，原文共 {len(text):,} 字符；可用 start_line/end_line 分段继续读取）"
    )
