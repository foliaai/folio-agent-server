#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""=================================================
@PROJECT_NAME: agent_apps
@File    : mysql_manager.py
@Author  : caixiongjiang
@Date    : 2026/1/21 20:03
@Function: 
    函数功能名称
@Modify History:
         
@Copyright：Copyright(c) 2024-2026. All Rights Reserved
=================================================="""

from typing import Optional
from urllib.parse import quote_plus
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from db.mysql.connection.base import BaseMySQLManager
from src.utils.env_manager import get_env_manager
from src.utils.config_manager import get_config_manager

env_manager = get_env_manager()
config_manager = get_config_manager()


class MySQLServerManager(BaseMySQLManager):
    """MySQL Server 连接管理器"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        """单例模式实现"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
            self,
            host: Optional[str] = None,
            port: Optional[int] = None,
            user: Optional[str] = None,
            password: Optional[str] = None,
            database: Optional[str] = None,
            pool_size: int = 5,
            max_overflow: int = 10,
            pool_timeout: int = 30,
            pool_recycle: int = 3600,
            echo: bool = False
    ):
        """
        初始化 MySQL Server 连接管理器

        Args:
            host: 数据库主机地址
            port: 数据库端口
            user: 数据库用户名
            password: 数据库密码
            database: 数据库名称
            pool_size: 连接池大小，默认 5
            max_overflow: 最大溢出连接数，默认 10
            pool_timeout: 连接池获取连接超时时间（秒），默认 30
            pool_recycle: 连接池回收时间（秒），默认 3600
            echo: 是否打印 SQL 语句，默认 False
        """
        if self._initialized:
            return

        super().__init__()

        # 从配置文件读取 MySQL 配置（host/database 由环境变量强制注入，与 AKS 同构）
        mysql_config = config_manager.get_mysql_config()
        mysql_auth = env_manager.get_mysql_auth()

        self.host = host or mysql_config.get("host")
        self.port = port or mysql_config.get("port")
        self.user = user or mysql_auth.get("user")

        # 密码：参数优先，其次环境变量（必填注入，无兜底）
        self.password = quote_plus(password or mysql_auth.get("password", ""))

        self.database = database or mysql_config.get("database")

        # 连接四要素必填（缺失直接抛错，不静默连 localhost/默认库）
        missing = [
            var
            for var, val in (
                ("MYSQL_HOST", self.host),
                ("MYSQL_USER", self.user),
                ("MYSQL_PASSWORD", self.password),
                ("MYSQL_DATABASE", self.database),
            )
            if not val
        ]
        if missing:
            raise ValueError(f"MySQL 配置缺失（检查 .env / 环境变量）: {', '.join(missing)}")

        # 连接池配置：优先使用参数，其次使用配置文件，最后使用默认值
        self.pool_size = pool_size if pool_size != 5 else mysql_config.get("pool_size", 5)
        self.max_overflow = max_overflow if max_overflow != 10 else mysql_config.get("max_overflow", 10)
        self.pool_timeout = pool_timeout if pool_timeout != 30 else mysql_config.get("pool_timeout", 30)
        self.pool_recycle = pool_recycle if pool_recycle != 3600 else mysql_config.get("pool_recycle", 3600)
        self.echo = echo if echo else mysql_config.get("echo", False)

        self.engine = self._create_engine()
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

        self._initialized = True
        logger.info(
            f"MySQL Server 连接管理器初始化成功: "
            f"{self.host}:{self.port}/{self.database}"
        )

    def get_db_url(self) -> str:
        """获取数据库连接 URL"""
        return (
            f"mysql+pymysql://{self.user}:{self.password}@"
            f"{self.host}:{self.port}/{self.database}"
        )

    def _create_engine(self) -> Engine:
        """创建数据库引擎"""
        db_url = self.get_db_url()

        engine = create_engine(
            db_url,
            echo=self.echo,
            future=True,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_timeout=self.pool_timeout,
            pool_recycle=self.pool_recycle,
            pool_pre_ping=True,  # 在使用连接前进行 ping 操作
            poolclass=QueuePool
        )

        return engine

    def create_database(self) -> None:
        """创建数据库（如果不存在）"""
        try:
            # 创建不指定数据库的引擎
            db_url_without_db = (
                f"mysql+pymysql://{self.user}:{self.password}@"
                f"{self.host}:{self.port}"
            )
            temp_engine = create_engine(db_url_without_db)

            # 创建数据库的 SQL 语句
            create_db_sql = text(
                f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )

            # 执行创建数据库
            with temp_engine.connect() as conn:
                conn.execute(create_db_sql)
                conn.commit()

            logger.info(f"数据库创建或已存在: {self.database}")
            temp_engine.dispose()
        except Exception as e:
            logger.error(f"创建数据库失败: {e}")
            raise
