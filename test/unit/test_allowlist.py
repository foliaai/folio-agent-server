#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""单元测试：http_fetch allowlist 与 SSRF 硬防护。"""

from src.sandbox.allowlist import check_url, is_private_host


class TestCheckUrl:
    def test_subdomain_allowed(self):
        ok, reason = check_url("https://api.example.com/v1", ["example.com"])
        assert ok, reason

    def test_exact_domain_allowed(self):
        ok, _ = check_url("https://example.com/", ["example.com"])
        assert ok

    def test_not_in_allowlist(self):
        ok, reason = check_url("https://evil.com/x", ["example.com"])
        assert not ok and reason == "not_in_allowlist"

    def test_suffix_not_subdomain(self):
        # badexample.com 不是 example.com 的子域
        ok, _ = check_url("https://badexample.com/x", ["example.com"])
        assert not ok

    def test_empty_allowlist_denies_all(self):
        ok, reason = check_url("https://example.com/x", [])
        assert not ok and reason == "empty_allowlist"

    def test_http_scheme_denied(self):
        ok, reason = check_url("http://api.example.com/v1", ["example.com"])
        assert not ok and reason == "scheme_not_https"

    def test_nonstandard_port_denied(self):
        ok, reason = check_url("https://api.example.com:8443/v1", ["example.com"])
        assert not ok and reason == "port_not_allowed"

    def test_ip_literal_denied_even_if_allowlisted(self):
        ok, reason = check_url("https://127.0.0.1/x", ["127.0.0.1"])
        assert not ok and reason == "ip_literal_denied"

    def test_private_host_denied_even_if_allowlisted(self):
        ok, _ = check_url("https://localhost/x", ["localhost"])
        assert not ok

    def test_malformed_url_denied(self):
        ok, reason = check_url("::::not-a-url", ["example.com"])
        assert not ok and reason == "bad_url"

    def test_allowlist_rule_normalization(self):
        # 规则大小写与前导点都应归一
        ok, _ = check_url("https://api.example.com/x", [".EXAMPLE.com"])
        assert ok


class TestPrivateHost:
    def test_loopback(self):
        assert is_private_host("127.0.0.1")
        assert is_private_host("localhost")

    def test_private_ranges(self):
        for h in ("10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.1.1", "::1", "fd00::1"):
            assert is_private_host(h), h

    def test_public_host_not_private(self):
        assert not is_private_host("example.com")
        assert not is_private_host("8.8.8.8")
