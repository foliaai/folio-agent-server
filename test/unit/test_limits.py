#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""单元测试：资源限制钳制（请求只能收紧、不能放宽平台默认与硬顶）。"""

from src.sandbox.contracts import SandboxLimits
from src.sandbox.limits import HARD_CEILINGS, clamp_limits, limits_from_config


def make(**kw) -> SandboxLimits:
    return SandboxLimits(**kw)


def test_defaults_from_empty_config():
    lim = limits_from_config({})
    assert lim.fuel_per_call == 1_000_000_000  # 实测校准：echo 一次 ~1e7，默认给百倍余量
    assert lim.wall_clock_timeout_ms == 10_000
    assert lim.memory_limit_mb == 64
    assert lim.output_limit_mb == 1


def test_config_override():
    lim = limits_from_config({"fuel_per_call": 500, "node_runner_timeout_ms": 20000})
    assert lim.fuel_per_call == 500
    assert lim.node_runner_timeout_ms == 20000


def test_looser_request_clamped_to_platform():
    platform = limits_from_config({})
    requested = make(fuel_per_call=10**12)
    assert clamp_limits(requested, platform).fuel_per_call == platform.fuel_per_call


def test_tighter_request_wins():
    platform = limits_from_config({})
    requested = make(fuel_per_call=1000, memory_limit_mb=8)
    clamped = clamp_limits(requested, platform)
    assert clamped.fuel_per_call == 1000
    assert clamped.memory_limit_mb == 8


def test_platform_above_ceiling_is_capped():
    platform = limits_from_config({"fuel_per_call": HARD_CEILINGS.fuel_per_call * 10})
    clamped = clamp_limits(make(), platform)
    assert clamped.fuel_per_call <= HARD_CEILINGS.fuel_per_call


def test_every_field_clamped():
    platform = limits_from_config({})
    too_big = make(
        fuel_per_call=10**15, wall_clock_timeout_ms=10**9, memory_limit_mb=10**6,
        output_limit_mb=10**6, http_fetch_max_body_mb=10**6,
        http_fetch_daily_quota=10**9, node_runner_timeout_ms=10**9,
    )
    clamped = clamp_limits(too_big, platform)
    for field in SandboxLimits.model_fields:
        assert getattr(clamped, field) <= max(
            getattr(platform, field), 1
        ), f"{field} not clamped: {getattr(clamped, field)}"
