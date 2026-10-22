#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : limits.py
@Author  : caixiongjiang
@Date    : 2026/10/02
@Function:
    沙箱资源限制的配置侧封装：config.toml [sandbox] → SandboxLimits。
    平台默认值是硬顶：请求级/agent 级覆盖只能收紧、不能放宽。
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Any, Dict

from src.sandbox.contracts import SandboxLimits


# 平台硬顶（写入代码而非配置：配置文件被改大也不允许越过红线）
HARD_CEILINGS = SandboxLimits(
    fuel_per_call=2_000_000_000,
    wall_clock_timeout_ms=60_000,
    memory_limit_mb=256,
    output_limit_mb=8,
    http_fetch_max_body_mb=20,
    http_fetch_daily_quota=10_000,
)


def limits_from_config(section: Dict[str, Any]) -> SandboxLimits:
    """从 config.toml [sandbox] 段构造平台默认限制"""
    return SandboxLimits(
        fuel_per_call=int(section.get("fuel_per_call", 1_000_000_000)),
        wall_clock_timeout_ms=int(section.get("wall_clock_timeout_ms", 10_000)),
        memory_limit_mb=int(section.get("memory_limit_mb", 64)),
        output_limit_mb=int(section.get("output_limit_mb", 1)),
        http_fetch_max_body_mb=int(section.get("http_fetch_max_body_mb", 5)),
        http_fetch_daily_quota=int(section.get("http_fetch_daily_quota", 1000)),
        node_runner_timeout_ms=int(section.get("node_runner_timeout_ms", 15_000)),
    )


def clamp_limits(requested: SandboxLimits, platform: SandboxLimits) -> SandboxLimits:
    """
    请求级限制 → 实际生效限制：取请求值与平台默认的较小值，再钳制到硬顶。

    规则：requested 值缺失（=模型默认）时用平台默认；显式给出的值只能比平台默认更严，
    更宽的请求一律压回平台默认（不报错——DIY 表单允许填小不许填大）。
    """

    def pick(req: int, plat: int, ceil: int) -> int:
        effective = min(req, plat) if req > 0 else plat
        return max(1, min(effective, ceil))

    return SandboxLimits(
        fuel_per_call=pick(requested.fuel_per_call, platform.fuel_per_call, HARD_CEILINGS.fuel_per_call),
        wall_clock_timeout_ms=pick(
            requested.wall_clock_timeout_ms, platform.wall_clock_timeout_ms, HARD_CEILINGS.wall_clock_timeout_ms
        ),
        memory_limit_mb=pick(requested.memory_limit_mb, platform.memory_limit_mb, HARD_CEILINGS.memory_limit_mb),
        output_limit_mb=pick(requested.output_limit_mb, platform.output_limit_mb, HARD_CEILINGS.output_limit_mb),
        http_fetch_max_body_mb=pick(
            requested.http_fetch_max_body_mb, platform.http_fetch_max_body_mb, HARD_CEILINGS.http_fetch_max_body_mb
        ),
        http_fetch_daily_quota=pick(
            requested.http_fetch_daily_quota, platform.http_fetch_daily_quota, HARD_CEILINGS.http_fetch_daily_quota
        ),
        node_runner_timeout_ms=pick(
            requested.node_runner_timeout_ms, platform.node_runner_timeout_ms, HARD_CEILINGS.node_runner_timeout_ms
        ),
    )
