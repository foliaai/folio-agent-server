#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : config_manager.py
@Author  : caixiongjiang
@Date    : 2026/1/19 15:37
@Function:
    config.toml 配置文件管理器
@Modify History:
    2026-10-02 清理旧业务段（milvus/neo4j/minio/embedding/mineru/file_upload/storage），保留 mysql/mongodb/redis/logging/sandbox
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

import toml
from pathlib import Path
from typing import Any, Dict, Optional, List
from copy import deepcopy
from loguru import logger

from src.utils.env_manager import EnvManager, get_env_manager


class ConfigManager:
    """配置文件管理器"""

    # 默认配置文件路径（相对于项目根目录）
    DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.toml"

    def __init__(self, config_file: Optional[str] = None):
        """
        初始化配置管理器

        Args:
            config_file: config.toml 文件路径，如果为None则使用默认路径
        """
        self._config_file = config_file
        self._config: Dict[str, Any] = {}
        self._env_manager = get_env_manager()
        self._load_config()

    def _load_config(self) -> None:
        """加载配置文件"""
        if self._config_file:
            config_path = Path(self._config_file)
        else:
            config_path = self.DEFAULT_CONFIG_PATH

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        try:
            self._config = toml.load(config_path)
            logger.info(f"已加载配置文件: {config_path}")
        except Exception as e:
            raise ValueError(f"配置文件加载失败: {e}")

    def reload(self) -> None:
        """重新加载配置文件"""
        self._load_config()
        logger.info("配置文件已重新加载")

    def get(self, path: str, default: Any = None) -> Any:
        """
        获取配置项（支持点号路径）

        Args:
            path: 配置路径，如 "sandbox.fuel_per_call"
            default: 默认值

        Returns:
            配置值
        """
        keys = path.split(".")
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """获取整个配置节（深拷贝）"""
        return deepcopy(self._config.get(section, {}))

    def has(self, path: str) -> bool:
        """检查配置项是否存在"""
        keys = path.split(".")
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return False

        return True

    def get_all(self) -> Dict[str, Any]:
        """获取所有配置（深拷贝）"""
        return deepcopy(self._config)

    # ==================== 配置节获取 ====================

    def _mongodb_section(self, env: EnvManager) -> Dict[str, Any]:
        config = self.get_section("mongodb")
        config["host"] = env.get_mongodb_host()
        config["database"] = env.get_mongodb_database()
        return config

    def get_mongodb_config(self) -> Dict[str, Any]:
        """获取MongoDB配置"""
        return self._mongodb_section(self._env_manager)

    def _mysql_section(self, env: EnvManager) -> Dict[str, Any]:
        """[mysql] 段 + 环境变量强制注入 host/database（与 AKS 同构：必填、唯一来源）"""
        config = self.get_section("mysql")
        config["host"] = env.get_required("MYSQL_HOST")
        config["database"] = env.get_required("MYSQL_DATABASE")
        return config

    def get_mysql_config(self) -> Dict[str, Any]:
        """获取MySQL配置（host/database 由环境变量注入，fail fast）"""
        return self._mysql_section(self._env_manager)

    def _redis_section(self, env: EnvManager) -> Dict[str, Any]:
        """[redis] 段 + 环境变量强制注入 host/db（与 AKS 同构；db 必填防连错库）"""
        config = self.get_section("redis")
        config["host"] = env.get_required("REDIS_HOST")
        config["db"] = int(env.get_required("REDIS_DB"))
        return config

    def get_redis_config(self) -> Dict[str, Any]:
        """获取Redis配置"""
        return self._redis_section(self._env_manager)

    def get_sandbox_config(self) -> Dict[str, Any]:
        """获取沙箱配置（资源限制与进程池）"""
        return self.get_section("sandbox")

    def get_logging_config(self) -> Dict[str, Any]:
        """获取日志配置"""
        return self.get_section("logging")

    # ==================== 配置验证 ====================

    def validate(self) -> Dict[str, List[str]]:
        """
        验证配置完整性

        Returns:
            验证结果，key为配置节名称，value为缺失的必需字段列表
        """
        required_configs = {
            "redis": ["host", "port"],
            "logging": ["level", "log_dir", "log_file"],
            "sandbox": [
                "fuel_per_call",
                "wall_clock_timeout_ms",
                "memory_limit_mb",
                "output_limit_mb",
            ],
        }

        validation_results = {}

        for section, required_fields in required_configs.items():
            missing_fields = []
            section_config = self.get_section(section)

            for field in required_fields:
                if field not in section_config:
                    missing_fields.append(field)

            if missing_fields:
                validation_results[section] = missing_fields

        return validation_results

    def check_health(self) -> bool:
        """检查配置健康状态"""
        validation_results = self.validate()

        if validation_results:
            for section, missing_fields in validation_results.items():
                logger.error(f"配置节 [{section}] 缺少必需字段: {', '.join(missing_fields)}")
            return False

        logger.info("配置健康检查通过")
        return True

    # ==================== 配置组装（结合环境变量） ====================


    def get_redis_full_config(self, env_manager: EnvManager) -> Dict[str, Any]:
        """获取完整的Redis配置（host/db 由 _redis_section 从环境变量注入）"""
        config = self.get_redis_config()
        config.update(env_manager.get_redis_auth())
        return config


# 创建全局单例
_config_manager_instance: Optional[ConfigManager] = None


def get_config_manager(config_file: Optional[str] = None) -> ConfigManager:
    """获取配置管理器单例"""
    global _config_manager_instance

    if _config_manager_instance is None:
        _config_manager_instance = ConfigManager(config_file)

    return _config_manager_instance


# 便捷函数
def get_config(path: str, default: Any = None) -> Any:
    """获取配置项的便捷函数"""
    return get_config_manager().get(path, default)


def get_config_section(section: str) -> Dict[str, Any]:
    """获取配置节的便捷函数"""
    return get_config_manager().get_section(section)
