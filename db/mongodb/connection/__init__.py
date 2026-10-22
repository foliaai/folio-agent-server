#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""MongoDB 连接管理层（motor 客户端 + Beanie 初始化，对齐 db/mysql/connection 职责）"""

from db.mongodb.connection.mongodb_manager import MongoDBManager, get_mongodb_manager

__all__ = ["MongoDBManager", "get_mongodb_manager"]
