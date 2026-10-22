#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : main.py
@Author  : caixiongjiang
@Date    : 2026/1/10 21:15
@Function:
    FastAPI 应用入口（folio agent-server，:8002）
    里程碑：M1 沙箱基线（本文件暂无业务路由，仅健康检查 + 沙箱生命周期）
@Modify History:
    2026-10-02 重写：端口 8001→8002（架构方案 D8）、清理旧业务（mineru/storage）、
              lifespan 接入沙箱进程池
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api import routes
from src.utils.config_manager import get_config_manager
from src.utils.env_manager import get_env_manager

config_manager = get_config_manager()
env_manager = get_env_manager()

app_name = "folio agent-server"
app_version = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：MySQL → MongoDB → 沙箱进程池 → 路由打印"""
    # 1. MySQL（agent_session 会话元信息；失败不阻断启动——对话流式不依赖库）
    try:
        from db.mysql.connection import get_mysql_manager

        db_manager = get_mysql_manager()
        db_manager.init_db()
        if db_manager.health_check():
            logger.info("✅ MySQL 初始化成功")
        else:
            logger.warning("⚠️  MySQL 健康检查失败（不阻断启动）")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️  MySQL 不可用（不阻断启动）: {e}")

    # 2. MongoDB（会话消息正文存储；AKS 同构 motor+Beanie。初始化失败
    #    不阻断启动——对话流式不依赖库，落库失败仅日志，与 MySQL 态度一致）
    mongo_manager = None
    try:
        from db.mongodb import get_mongodb_manager

        mongo_manager = await get_mongodb_manager()
        if await mongo_manager.is_connected():
            logger.info("✅ MongoDB 初始化成功")
        else:
            logger.warning("⚠️  MongoDB 健康检查失败（落库将在使用时报错）")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️  MongoDB 不可用（不阻断启动）: {e}")

    # 3. 沙箱进程池
    sandbox_client = None
    if env_manager.is_sandbox_enabled():
        try:
            from src.sandbox.client import start_sandbox

            sandbox_client = await start_sandbox(sandbox_config=config_manager.get_sandbox_config())
            logger.info(f"✅ 沙箱进程池就绪: {sandbox_client._manager.stats}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"❌ 沙箱进程池启动失败: {e}")
    else:
        logger.info("⏭️  沙箱未启用（SANDBOX_ENABLED=false）")

    # 4. 工作空间清理任务（设计 §3.4：每日一次，APScheduler 进程内单实例）
    cleanup_scheduler = None
    try:
        from src.service.workspace.cleanup import start_cleanup_scheduler

        cleanup_scheduler = start_cleanup_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️  工作空间清理任务启动失败（不阻断）: {e}")

    # 5. 路由概览
    logger.info(f"🚀 {app_name} v{app_version} 启动完成 | docs: http://localhost:8002/docs")

    # 6. 本地联调鉴权旁路（DEV_AUTH_BYPASS=1 且非 production）
    try:
        from api.dependencies.auth import install_dev_bypass

        install_dev_bypass(app)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"dev bypass check skipped: {e}")

    yield

    logger.info(f"👋 {app_name} 正在关闭...")
    if cleanup_scheduler is not None:
        try:
            cleanup_scheduler.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"清理任务关闭忽略: {e}")
    if sandbox_client is not None:
        try:
            from src.sandbox.client import stop_sandbox

            await stop_sandbox()
        except Exception as e:  # noqa: BLE001
            logger.error(f"沙箱关闭失败: {e}")
    if mongo_manager is not None:
        try:
            await mongo_manager.disconnect()
        except Exception as e:  # noqa: BLE001
            logger.error(f"MongoDB 连接关闭失败: {e}")
    try:
        from db.mysql.connection import MySQLManagerFactory

        MySQLManagerFactory.close_all()
    except Exception as e:  # noqa: BLE001
        logger.error(f"MySQL 连接关闭失败: {e}")


app = FastAPI(
    title=app_name,
    version=app_version,
    description="folio agent-server：智能体平台后端（统一运行时 + WASM 沙箱）",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)


@app.get("/health", tags=["Health"])
async def health_check():
    """健康检查接口（直连调试用；经网关请用 /api/v1/health）"""
    return await _health_payload()


@app.get("/api/v1/health", tags=["Health"])
async def health_check_gw():
    """健康检查（网关路径：Caddy 只转发 /api/v1/*）"""
    return await _health_payload()


async def _health_payload() -> dict:
    sandbox_status = "disabled"
    try:
        from src.sandbox.client import _client

        if _client is not None:
            sandbox_status = _client._manager.stats
    except Exception:  # noqa: BLE001
        pass
    return {
        "status": "healthy",
        "app_name": app_name,
        "version": app_version,
        "sandbox": sandbox_status,
        "config_status": "ok" if config_manager.check_health() else "warning",
    }


if __name__ == "__main__":
    import uvicorn

    logger.info(f"🌍 运行环境: {env_manager.get_app_env()} | 调试: {env_manager.is_debug()}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=env_manager.is_debug(),
        log_level="info",
    )
