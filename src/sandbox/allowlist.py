#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : allowlist.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    http_fetch 域名 allowlist 与 SSRF 硬防护。
    规则（默认全拒）：
      - "example.com"  → example.com 及其所有子域
      - 空 allowlist   → 一律拒绝
    硬性拒绝（allowlist 配了也拒）：
      - 非 https scheme、非默认端口
      - IP 字面量（含十进制/十六进制变体）、localhost
      - 私网/保留地址段（127/8、10/8、172.16/12、192.168/16、169.254/16、::1、fc00::/7 等）
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import ipaddress
import socket
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

import tldextract  # type: ignore


def _extract_host(url: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """
    解析 URL → (scheme, hostname, port)。

    Returns:
        解析失败或缺少 host 时返回 None
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None

    if not parts.hostname:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    return parts.scheme.lower(), parts.hostname.lower(), port


def is_private_host(hostname: str) -> bool:
    """IP 字面量与私网/保留地址判定（域名不在本函数职责内，由 DNS 解析侧兜底）"""
    if hostname in ("localhost",) or hostname.endswith(".localhost"):
        return True

    # IP 字面量（v4/v6，含十六进制等变体——socket.inet_pton 只接受规范形式，
    # 变体形式视为非法一律拒绝，见 check_url 的 not is_ip 判定）
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False

    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def check_url(url: str, allowlist: List[str]) -> Tuple[bool, str]:
    """
    检查一次 http_fetch 的目标 URL 是否放行。

    Args:
        url: 目标 URL
        allowlist: 域名 allowlist（["example.com"] 语义 = 该域及其子域）

    Returns:
        (allowed, reason)：allowed=False 时 reason 为拒绝原因
    """
    parsed = _extract_host(url)
    if parsed is None:
        return False, "bad_url"
    scheme, hostname, port = parsed

    if scheme != "https":
        return False, "scheme_not_https"
    if port is not None and port != 443:
        return False, "port_not_allowed"
    if _is_ip_literal(hostname):
        return False, "ip_literal_denied"
    if is_private_host(hostname):
        return False, "private_host"

    if not allowlist:
        return False, "empty_allowlist"

    host_reg = tldextract.extract(hostname)
    registered = host_reg.top_domain_under_public_suffix or ""
    if not registered:
        return False, "unregistrable_host"

    for rule in allowlist:
        rule = rule.strip().lower().lstrip(".")
        if not rule:
            continue
        if hostname == rule or hostname.endswith("." + rule):
            return True, "ok"

    return False, "not_in_allowlist"


def resolve_and_check(hostname: str, allowlist: List[str]) -> Tuple[bool, str]:
    """
    DNS 解析后的二次 SSRF 检查（allowlist 域名解析到私网 IP 的场景，防 DNS rebinding）。

    在 hostfuncs 发起真实请求前调用；解析失败同样拒绝。
    """
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "dns_failed"

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "resolves_to_private"

    # allowlist 域名检查在 check_url 已完成，这里只需网络层面安全
    return True, "ok"
