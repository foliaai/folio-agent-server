#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""分型路由 + 嗅探单测（设计 §4.1 / D10 / D19）"""

import pytest

from src.service.workspace.file_types import FileRejectError, route_file


class TestExecutableReject:
    def test_pe_magic_rejected(self):
        with pytest.raises(FileRejectError):
            route_file(b"MZ\x90\x00" + b"\x00" * 60, "innocent.pdf")

    def test_elf_rejected(self):
        with pytest.raises(FileRejectError):
            route_file(b"\x7fELF" + b"\x00" * 60, "lib.so")

    def test_exe_extension_rejected_even_text_content(self):
        with pytest.raises(FileRejectError):
            route_file(b"plain text" + b" " * 54, "setup.exe")

    def test_macho_rejected(self):
        with pytest.raises(FileRejectError):
            route_file(b"\xfe\xed\xfa\xcf" + b"\x00" * 60, "binary.bin")


class TestSniffing:
    def test_png_magic_routes_image_even_with_pdf_name(self):
        kind, route, mime = route_file(b"\x89PNG\r\n\x1a\n" + b"\x00" * 54, "fake.pdf")
        assert (kind, route) == ("image", "image")
        assert mime == "image/png"

    def test_pdf_magic(self):
        kind, route, mime = route_file(b"%PDF-1.7\n" + b" " * 55, "doc.pdf")
        assert (kind, route, mime) == ("file", "mineru", "application/pdf")

    def test_fake_image_extension_rejected(self):
        # 扩展名声称图片、内容不是 → 拒（防伪装）
        with pytest.raises(FileRejectError):
            route_file(b"just plain text bytes here...." + b" " * 32, "photo.png")


class TestRouting:
    def test_docx_to_mineru(self):
        kind, route, _ = route_file(b"PK\x03\x04" + b"\x00" * 60, "报告.docx")
        assert (kind, route) == ("file", "mineru")

    def test_xlsx_raw(self):
        # D19：xlsx 明确归数据档
        kind, route, _ = route_file(b"PK\x03\x04" + b"\x00" * 60, "数据.xlsx")
        assert (kind, route) == ("file", "raw")

    def test_md_direct(self):
        kind, route, _ = route_file("# 标题\n正文".encode("utf-8"), "笔记.md")
        assert (kind, route) == ("file", "direct_md")

    def test_txt_direct(self):
        kind, route, _ = route_file(b"hello world" + b" " * 53, "日志.txt")
        assert (kind, route) == ("file", "direct_md")

    def test_csv_raw(self):
        kind, route, _ = route_file(b"a,b,c\n1,2,3", "data.csv")
        assert (kind, route) == ("file", "raw")

    def test_unknown_ext_falls_to_raw(self):
        kind, route, _ = route_file(b"\x00\x01binary" + b" " * 54, "model.ckpt")
        assert (kind, route) == ("file", "raw")
