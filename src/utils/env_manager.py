#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : env_manager.py
@Author  : caixiongjiang
@Date    : 2026/1/19 15:37
@Function:
    环境变量管理器（.env + os.environ）
@Modify History:
    2026-10-02 清理旧业务变量（milvus/mongodb/neo4j/minio/embedding/mineru/各家模型key），
              收敛为 mysql/mongodb/redis 认证 + 平台服务地址 + 沙箱开关
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import os
from pathlib import Path
from typing import Optional, Dict, List
from dotenv import load_dotenv
from loguru import logger


class EnvManager:
    """环境变量管理器"""

    # 默认环境变量文件路径（相对于项目根目录）
    DEFAULT_ENV_PATH = Path(__file__).parent.parent.parent / ".env"

    # 必需的环境变量列表
    _critical_vars = {
        "APP_SECRET_KEY",
        "APP_ENV",
    }

    def __init__(self, env_file: Optional[str] = None):
        """
        初始化环境变量管理器

        Args:
            env_file: .env 文件路径，如果为None则使用默认路径
        """
        self._env_file = env_file
        self._env_vars: Dict[str, str] = {}
        self._load_env()

    def _load_env(self) -> None:
        """加载环境变量"""
        if self._env_file:
            env_path = Path(self._env_file)
        else:
            env_path = self.DEFAULT_ENV_PATH

        if not env_path.exists():
            logger.warning(f"环境变量文件不存在: {env_path}，将仅使用系统环境变量")
            return

        load_dotenv(env_path, override=True)
        logger.info(f"已加载环境变量文件: {env_path}")

        self._cache_env_vars()

    def _cache_env_vars(self) -> None:
        """缓存环境变量到内存"""
        self._env_vars = dict(os.environ)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """获取环境变量"""
        return os.getenv(key, default)

    def get_required(self, key: str) -> str:
        """
        获取必需的环境变量，不存在则抛异常

        Raises:
            ValueError: 如果环境变量不存在
        """
        value = self.get(key)
        if value is None:
            raise ValueError(f"必需的环境变量未设置: {key}")
        return value

    def get_int(self, key: str, default: Optional[int] = None) -> Optional[int]:
        """获取整数类型的环境变量"""
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            logger.warning(f"环境变量 {key} 无法转换为整数: {value}，使用默认值 {default}")
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """获取布尔类型的环境变量"""
        value = self.get(key)
        if value is None:
            return default

        true_values = {"true", "1", "yes", "on", "t", "y"}
        false_values = {"false", "0", "no", "off", "f", "n"}

        value_lower = value.lower().strip()
        if value_lower in true_values:
            return True
        elif value_lower in false_values:
            return False
        else:
            logger.warning(f"环境变量 {key} 的值 '{value}' 无法识别为布尔值，使用默认值 {default}")
            return default

    def get_list(self, key: str, separator: str = ",", default: Optional[List[str]] = None) -> List[str]:
        """获取列表类型的环境变量"""
        value = self.get(key)
        if value is None:
            return default or []

        return [item.strip() for item in value.split(separator) if item.strip()]

    def validate_required_vars(self, required_vars: Optional[List[str]] = None) -> Dict[str, bool]:
        """验证必需的环境变量是否都已设置"""
        if required_vars is None:
            required_vars = list(self._critical_vars)

        results = {}
        for var in required_vars:
            results[var] = self.get(var) is not None

        return results

    def check_health(self) -> bool:
        """检查环境变量健康状态"""
        validation_results = self.validate_required_vars()
        missing_vars = [var for var, exists in validation_results.items() if not exists]

        if missing_vars:
            logger.error(f"缺少必需的环境变量: {', '.join(missing_vars)}")
            return False

        logger.info("环境变量健康检查通过")
        return True

    # ==================== 数据库认证信息获取 ====================

    def get_mysql_auth(self) -> Dict[str, str]:
        """获取MySQL认证信息（必填：缺失直接抛错，不猜默认账号）"""
        return {
            "user": self.get_required("MYSQL_USER"),
            "password": self.get_required("MYSQL_PASSWORD"),
        }

    def get_mongodb_auth(self) -> Dict[str, str]:
        """获取MongoDB认证信息（AKS 同款：MONGODB_URI 优先，否则账号密码）"""
        uri = self.get("MONGODB_URI")
        if uri:
            return {"uri": uri}

        return {
            "user": self.get("MONGODB_USER", ""),
            "password": self.get("MONGODB_PASSWORD", ""),
            "auth_source": self.get("MONGODB_AUTH_SOURCE", "admin"),
        }

    def get_mongodb_host(self) -> str:
        return self.get_required("MONGODB_HOST")

    def get_mongodb_database(self) -> str:
        return self.get_required("MONGODB_DATABASE")

    def get_redis_auth(self) -> Dict[str, str]:
        """获取Redis认证信息"""
        return {
            "username": self.get("REDIS_USERNAME", ""),
            "password": self.get("REDIS_PASSWORD", ""),
        }

    # ==================== 平台服务地址 ====================

    def get_tavily_api_key(self) -> Optional[str]:
        """获取Tavily搜索API Key（M2 web_search 工具）"""
        return self.get("TAVILY_API_KEY")

    def get_aks_base_url(self) -> str:
        """获取知识引擎 AKS 地址（M2 kb_retrieval 工具）"""
        return self.get("AKS_BASE_URL", "http://localhost:8000")

    def get_skill_service_url(self) -> str:
        """获取技能服务地址（M4 技能层）"""
        return self.get("SKILL_SERVICE_URL", "http://localhost:8001")

    # ==================== 沙箱 ====================

    def is_sandbox_enabled(self) -> bool:
        """沙箱功能开关"""
        return self.get_bool("SANDBOX_ENABLED", True)

    def get_sandbox_node_bin(self) -> str:
        """Pyodide 引擎的 Node 可执行路径"""
        return self.get("SANDBOX_NODE_BIN", "node")

    def get_sandbox_audit_dir(self) -> str:
        """沙箱审计日志目录"""
        return self.get("SANDBOX_AUDIT_DIR", "logs/sandbox")

    # ==================== 系统配置 ====================

    def get_app_env(self) -> str:
        """获取应用环境"""
        return self.get("APP_ENV", "development")

    def is_debug(self) -> bool:
        """是否为调试模式"""
        return self.get_bool("DEBUG", True)

    def get_app_secret_key(self) -> str:
        """获取应用密钥"""
        return self.get_required("APP_SECRET_KEY")


# 创建全局单例
_env_manager_instance: Optional[EnvManager] = None


def get_env_manager(env_file: Optional[str] = None) -> EnvManager:
    """获取环境变量管理器单例"""
    global _env_manager_instance

    if _env_manager_instance is None:
        _env_manager_instance = EnvManager(env_file)

    return _env_manager_instance


# 便捷函数
def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """获取环境变量的便捷函数"""
    return get_env_manager().get(key, default)


def get_required_env(key: str) -> str:
    """获取必需环境变量的便捷函数"""
    return get_env_manager().get_required(key)
