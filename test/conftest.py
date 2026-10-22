#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""沙箱测试公共 fixture。

沙箱进程池挂在一个专属 event loop（后台线程）上，测试以同步方式调用——
规避 pytest-asyncio 的 function/session loop 割裂问题。
"""

import asyncio
import base64
import subprocess
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
TOOLS_WASM = PROJECT_ROOT / "tools" / "wasm"


def build_or_load_wasm(name: str) -> str:
    """样例名 → base64 wasm（首次自动编译，产物缓存于 /tmp）"""
    out = Path(f"/tmp/sbx-{name}.wasm")
    if not out.exists():
        subprocess.run(
            ["npx", "tsx", "build.ts", f"samples/{name}.js", str(out)],
            cwd=TOOLS_WASM, check=True, capture_output=True, timeout=180,
        )
    return base64.b64encode(out.read_bytes()).decode()


def wasm_available(name: str) -> bool:
    """编译链（node/npm）不可用时跳过 JS 路径测试"""
    try:
        build_or_load_wasm(name)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


class ThreadedSandbox:
    """把 async SandboxClient 暴露为同步接口（跑在专属后台 loop 上）"""

    def __init__(self, client, loop):
        self._client = client
        self._loop = loop

    def execute(self, **kwargs):
        fut = asyncio.run_coroutine_threadsafe(self._client.execute(**kwargs), self._loop)
        return fut.result(timeout=180)

    @property
    def manager(self):
        return self._client._manager


@pytest.fixture(scope="session")
def wasm_b64():
    return build_or_load_wasm


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory):
    from src.sandbox.client import start_sandbox, stop_sandbox
    from src.utils.config_manager import get_config_manager

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="sandbox-test-loop")
    thread.start()

    audit_dir = tmp_path_factory.mktemp("sandbox-audit")
    cfg = get_config_manager().get_sandbox_config()
    client = asyncio.run_coroutine_threadsafe(
        start_sandbox(sandbox_config=cfg, audit_dir=str(audit_dir)), loop
    ).result(timeout=120)

    yield ThreadedSandbox(client, loop)

    asyncio.run_coroutine_threadsafe(stop_sandbox(), loop).result(timeout=60)
    loop.call_soon_threadsafe(loop.stop)
