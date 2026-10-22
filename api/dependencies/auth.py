#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : auth.py（api/dependencies）
@Author  : caixiongjiang
@Date    : 2026/10/03
@Function:
    认证依赖（纯委托 folio-auth-core 统一验签，对齐 AKS 模式）。
    签发完全由 folio-auth-server 承担，本服务只验 RS256 JWT。
    环境变量：AUTH_SERVER_URL（默认 http://localhost:8003）
@Modify History:

@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from fastapi import HTTPException, Request
from loguru import logger

from auth_core import AuthCoreSettings, configure_verifier
from auth_core.deps import (
    close_unauthorized,
    get_current_user_id,
    get_current_user_id_from_token,
    get_current_user_id_ws,
)

from src.utils.env_manager import get_env_manager

__all__ = [
    "get_current_user_id",
    "get_current_user_id_from_token",
    "get_current_user_id_ws",
    "close_unauthorized",
]


def _configure_auth_core() -> None:
    """按本服务环境装配 auth-core 校验器（import 时执行一次）"""
    env = get_env_manager()
    server_base = (env.get("AUTH_SERVER_URL") or "").strip().rstrip("/") or "http://localhost:8003"
    configure_verifier(
        AuthCoreSettings(jwks_url=f"{server_base}/api/auth/jwks")
    )


async def _dev_bypass_user_id(request: Request) -> str:
    """
    身份感知旁路（仅 DEV_AUTH_BYPASS 激活时被装进 dependency_overrides）：
    - 无凭证 → dev-user（旁路本职：本地无 auth-server 也能联调）；
    - 带有效 Bearer JWT → 验签后返回**真实** user_id（真实账号流量与联调
      dev-user 天然隔离，不互相串会话）；
    - 带了凭证但验不过 → 401。客户端声称了身份却无效属于客户端故障，
      响亮失败而非静默改写成 dev-user——否则过期 token 的流量会悄悄落进
      dev-user 命名空间（会话污染的隐蔽变体）。

    这段代码在生产永不执行（install_dev_bypass 有 APP_ENV=production 硬拒绝）。
    """
    authorization = request.headers.get("Authorization", "")
    scheme, _, param = authorization.partition(" ")
    token = param.strip() if scheme.strip().lower() == "bearer" else ""
    if not token:
        return "dev-user"
    try:
        from auth_core.verifier import get_verifier

        user = await get_verifier().verify(token)
        return user.user_id
    except Exception as e:  # noqa: BLE001 —— 带了无效凭证：按正常链路语义拒绝
        logger.warning(f"dev bypass: token 验签失败，拒绝（401）: {e}")
        raise HTTPException(status_code=401, detail="无效的登录凭证") from e


def install_dev_bypass(app) -> bool:
    """
    本地联调旁路（与 WS 路由的 DEV_AUTH_BYPASS 同一开关）：
    开启时把 HTTP 鉴权依赖替换为 _dev_bypass_user_id（身份感知，见其
    docstring）。仅非 production 环境生效；返回是否已安装。
    """
    from api.dependencies._env_file_flag import env_file_flag

    # DEV_AUTH_BYPASS 的唯一事实源是 .env 文件（本地联调专用开关，生产硬
    # 拒绝）。不走 os.environ/env_manager：dotenv 只加不清，同进程 uvicorn
    # reload 后旧值残留在 os.environ 里，删行不生效——直读文件后删行+touch
    # 即可切换，无需重启服务进程
    if env_file_flag("DEV_AUTH_BYPASS") not in ("1", "true", "yes"):
        return False
    from src.utils.env_manager import get_env_manager

    if get_env_manager().get_app_env() == "production":
        logger.error("DEV_AUTH_BYPASS is set but APP_ENV=production — refusing")
        return False
    from auth_core.deps import get_current_user_id as _dep

    app.dependency_overrides[_dep] = _dev_bypass_user_id
    logger.warning("DEV_AUTH_BYPASS active: HTTP auth bypassed (dev only, identity-aware)")
    return True


_configure_auth_core()
