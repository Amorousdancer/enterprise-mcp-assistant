#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置管理模块

支持从环境变量 / .env 文件加载配置，提供统一的配置访问接口。
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    # 项目根目录
    _ROOT = Path(__file__).parent.parent
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


@dataclass
class MySQLConfig:
    host: str = ""
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "enterprise_db"

    def __post_init__(self):
        self.host = self.host or os.getenv("MYSQL_HOST", "localhost")
        self.port = int(os.getenv("MYSQL_PORT", str(self.port)))
        self.user = self.user or os.getenv("MYSQL_USER", "root")
        self.password = self.password or os.getenv("MYSQL_PASSWORD", "")
        self.database = self.database or os.getenv("MYSQL_DATABASE", "enterprise_db")


@dataclass
class RedisConfig:
    host: str = ""
    port: int = 6379
    db: int = 0
    password: str = ""

    def __post_init__(self):
        self.host = self.host or os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", str(self.port)))
        self.db = int(os.getenv("REDIS_DB", str(self.db)))
        self.password = self.password or os.getenv("REDIS_PASSWORD", "")


@dataclass
class CSVConfig:
    data_dir: str = ""

    def __post_init__(self):
        if not self.data_dir:
            self.data_dir = os.getenv(
                "CSV_DATA_DIR",
                str(Path(__file__).parent.parent / "sample_data")
            )


@dataclass
class LLMConfig:
    model: str = ""
    api_key: str = ""
    base_url: str = ""

    def __post_init__(self):
        self.model = self.model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.api_key = self.api_key or os.getenv("DEEPSEEK_API_KEY", os.getenv("OPENAI_API_KEY", ""))
        self.base_url = self.base_url or os.getenv(
            "DEEPSEEK_BASE_URL",
            os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        )


@dataclass
class AppConfig:
    """应用总配置"""
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    csv: CSVConfig = field(default_factory=CSVConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    debug: bool = False

    def __post_init__(self):
        self.debug = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")


def load_config() -> AppConfig:
    """加载配置的统一入口"""
    return AppConfig()
