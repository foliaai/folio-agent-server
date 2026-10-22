#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""读视图纯函数单测（§4.3 存根 / §4.4 链接重写 / D13 分节）"""

from src.service.workspace.file_views import (
    build_attachments_block,
    build_file_stub,
    build_md_toc,
    clamp_for_model,
    extract_asset_names,
    find_section,
    latex_to_math_html,
    line_window,
    rebuild_md_from_content_list,
    rewrite_md_image_links,
    split_md_sections,
)


class TestStub:
    def test_stub_doc(self):
        info = {
            "file_id": "ab12cd34", "name": "季度报告.pdf", "kind": "file",
            "route": "mineru", "size": 2_100_000, "md_chars": 12_345,
        }
        s = build_file_stub(info, md_head="第一页内容 摘要开头")
        assert "季度报告.pdf" in s and "2.0MB" in s and "12,345" in s
        assert "[file_id: ab12cd34]" in s and "摘要" in s

    def test_stub_image_no_md(self):
        info = {"file_id": "ff", "name": "架构.png", "kind": "image", "route": "image", "size": 340_000}
        s = build_file_stub(info)
        assert "332.0KB" in s and "摘要" not in s

    def test_block(self):
        assert build_attachments_block([]) == ""
        b = build_attachments_block(["- a", "- b"])
        assert b.startswith("[本次会话附件]") and "- b" in b


class TestMdRewrite:
    def test_links_rewritten(self):
        md = "# t\n\n![](images/fig_0.png)\n\n![](images/p1_2.jpg)"
        out = rewrite_md_image_links(md)
        assert "](assets/fig_0.png)" in out and "](assets/p1_2.jpg)" in out
        assert "images/" not in out

    def test_html_img_rewritten(self):
        md = '<img src="images/x_0.png" alt=""/>'
        out = rewrite_md_image_links(md)
        assert 'src="assets/x_0.png"' in out

    def test_extract_names(self):
        md = "![](images/a.png)\n![](images/a.png)\n![](images/b.jpg)"
        assert extract_asset_names(rewrite_md_image_links(md)) == ["a.png", "b.jpg"]


MD = """引导段落

# 第一章

第一章内容。

## 1.1 小节

小节内容。

# 第二章

第二章内容。
"""


class TestSections:
    def test_split_by_h1_h2(self):
        secs = split_md_sections(MD)
        titles = [t for t, _ in secs]
        assert titles[0] == "(开头)"
        assert "第一章" in titles and "第二章" in titles

    def test_find_section_substring(self):
        body = find_section(MD, "1.1")
        assert body is not None and "小节内容" in body

    def test_find_section_miss(self):
        assert find_section(MD, "不存在的节") is None

    def test_line_window(self):
        assert line_window(MD, 1, 2).startswith("引导段落")
        assert line_window(MD, 99).count("\n") == 0  # 越界取空
        assert line_window(MD).startswith("引导段落")  # 全文默认


class TestClamp:
    def test_no_clamp(self):
        assert clamp_for_model("short") == "short"

    def test_clamp_marker(self):
        out = clamp_for_model("x" * 30_000, max_chars=100)
        assert out.startswith("x" * 100) and "已截断" in out and "30,000" in out



class TestRebuildFromContentList:
    CL = [
        {"type": "text", "text": "总标题", "text_level": "1"},
        {"type": "text", "text": "正文一段。"},
        {"type": "table", "table_caption": ["表 1 示例"], "table_footnote": ["注：口径。"],
         "table_body": "<table><tr><td>面积</td><td> ${ \\mathsf S } = \\pi r ^ { 2 }$ </td></tr></table>"},
        {"type": "text", "text": "注：被归类为普通文本的脚注行。"},
        {"type": "text", "text": "图 2 独立成行的图题注"},
        {"type": "equation", "text": "$$\nS = a / b\n$$"},
        {"type": "image", "img_path": "images/abc.jpg", "image_caption": ["图 1 示意"]},
        {"type": "mystery", "content": "未知类型兜底"},
    ]

    def test_headings_from_text_level(self):
        out = rebuild_md_from_content_list(self.CL)
        assert "# 总标题" in out

    def test_table_semantic(self):
        out = rebuild_md_from_content_list(self.CL)
        assert '<p class="md-caption">表 1 示例</p>' in out
        assert '<p class="md-footnote">注：口径。</p>' in out
        assert "<table>" in out

    def test_table_latex_converted_to_math_element(self):
        out = rebuild_md_from_content_list(self.CL)
        assert '<code class="language-math math-inline">' in out
        assert "$" not in out.split("<table>")[1].split("</table>")[0]

    def test_image_caption_new_field_name(self):
        out = rebuild_md_from_content_list(self.CL)
        assert "![](images/abc.jpg)" in out
        assert '<p class="md-caption">图 1 示意</p>' in out

    def test_stray_caption_footnote_text_heuristic_in_rebuild(self):
        out = rebuild_md_from_content_list(self.CL)
        assert '<p class="md-footnote">注：被归类为普通文本的脚注行。</p>' in out
        assert '<p class="md-caption">图 2 独立成行的图题注</p>' in out

    def test_equation_and_unknown_passthrough(self):
        out = rebuild_md_from_content_list(self.CL)
        assert "$$\nS = a / b\n$$" in out
        assert "未知类型兜底" in out

    def test_empty(self):
        assert rebuild_md_from_content_list([]) == ""
        assert rebuild_md_from_content_list(None) == ""


class TestLatexToMathHtml:
    def test_inline(self):
        out = latex_to_math_html("<td>值 $a + b$ 单元格</td>")
        assert '<code class="language-math math-inline">a + b</code>' in out

    def test_display(self):
        out = latex_to_math_html("<p>$$E = mc^2$$</p>")
        assert '<code class="language-math math-display">E = mc^2</code>' in out

    def test_no_dollar_untouched(self):
        src = "<td>价格 5 美元的文本</td>"
        assert latex_to_math_html(src) == src

    def test_caption_with_latex(self):
        from src.service.workspace.file_views import _md_caption
        assert "math-inline" in _md_caption("图 1 $y=f(x)$ 曲线")


class TestLatexMultilineDisplay:
    def test_display_multiline_in_table_body(self):
        body = "<table><tr><td>命中率</td><td>$$\nS = \\Sigma \\frac{c_i}{t_i}\n$$</td></tr></table>"
        out = latex_to_math_html(body)
        assert '<code class="language-math math-display">' in out
        assert "$$" not in out


class TestMdToc:
    def test_toc_hierarchy_and_indent(self):
        md = "# 概述\n正文\n## 背景\n正文\n## 目标\n# 数据\n## 分析\n正文"
        toc = build_md_toc(md)
        lines = toc.splitlines()
        assert lines[0] == "概述"
        assert lines[1] == "  背景" and lines[2] == "  目标"
        assert lines[3] == "数据" and lines[4] == "  分析"

    def test_toc_matches_find_section(self):
        # 目录里的标题必须能被 find_section 命中（同口径保障）
        md = "# Product Overview\nbody\n## Available Part Numbers\ntable\n"
        toc = build_md_toc(md)
        for title in [ln.strip() for ln in toc.splitlines()]:
            assert find_section(md, title) is not None

    def test_toc_empty_when_no_headings(self):
        assert build_md_toc("纯文本\n没有标题") == ""

    def test_toc_truncation_marker(self):
        md = "\n".join(f"# 第{i}章 标题" for i in range(1, 40))
        toc = build_md_toc(md)
        assert "已截断" in toc and "39 个标题" in toc

    def test_stub_with_toc(self):
        info = {"file_id": "ab12", "name": "报告.pdf", "kind": "file", "route": "mineru",
                "size": 1000, "md_chars": 500}
        s = build_file_stub(info, md_head="开头", toc="概述\n  背景")
        assert "目录" in s and "  背景" in s
        # 目录块整体缩进在存根行之下
        assert s.splitlines()[1].startswith("  目录")

    def test_stub_without_toc_unchanged(self):
        info = {"file_id": "ab12", "name": "a.pdf", "kind": "file", "route": "mineru", "size": 1000}
        s = build_file_stub(info)
        assert "目录" not in s
