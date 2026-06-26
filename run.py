#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键启动脚本

功能:
1. 检查 Python 依赖
2. 检查环境配置
3. 启动 MCP Servers
4. 启动 Gradio Web 界面

使用方式:
    python run.py
"""

import os
import sys
import json
import subprocess
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).parent


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   🤖 基于企业级 MCP 协议的异构数据智能助理                       ║
║                                                              ║
║   数据源: MySQL + CSV/Excel + Redis                          ║
║   协议:   MCP (Model Context Protocol)                       ║
║   模型:   DeepSeek                                           ║
║   前端:   Gradio Web UI                                      ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


def check_dependencies() -> bool:
    """检查必要的 Python 依赖"""
    print("📦 检查依赖...")

    required = [
        ("gradio", "gradio"),
        ("pymysql", "pymysql"),
        ("redis", "redis"),
        ("pandas", "pandas"),
        ("mcp", "mcp"),
        ("openai", "openai"),
        ("litellm", "litellm"),
    ]

    missing = []
    for module_name, pip_name in required:
        try:
            __import__(module_name)
            print(f"  ✅ {pip_name}")
        except ImportError:
            print(f"  ❌ {pip_name} — 未安装")
            missing.append(pip_name)

    if missing:
        print(f"\n⚠️  缺少依赖，请执行:")
        print(f"  pip install {' '.join(missing)}")
        return False

    print("  ✅ 所有依赖已安装\n")
    return True


def check_env() -> bool:
    """检查环境配置"""
    print("🔑 检查环境配置...")

    # 尝试加载 .env
    try:
        from dotenv import load_dotenv
        env_file = ROOT_DIR / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            print(f"  ✅ 已加载 .env 文件")
        else:
            print(f"  ⚠️  .env 文件不存在，将使用环境变量")
    except ImportError:
        pass

    # 检查 API Key
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        print(f"  ✅ API Key: {masked}")
    else:
        print("  ❌ 未找到 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")
        print("     请创建 .env 文件:")
        print("     DEEPSEEK_API_KEY=your_api_key_here")
        return False

    # 检查 MySQL 配置
    mysql_host = os.getenv("MYSQL_HOST", "localhost")
    mysql_db = os.getenv("MYSQL_DATABASE", "enterprise_db")
    print(f"  📊 MySQL: {mysql_host}/{mysql_db}")

    # 检查 Redis 配置
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = os.getenv("REDIS_PORT", "6379")
    print(f"  📊 Redis: {redis_host}:{redis_port}")

    print()
    return True


def check_sample_data() -> bool:
    """检查示例数据"""
    print("📁 检查示例数据...")

    sample_dir = ROOT_DIR / "sample_data"
    if not sample_dir.exists():
        print("  ⚠️  sample_data 目录不存在")
        return False

    csv_files = list(sample_dir.glob("*.csv"))
    sql_files = list(sample_dir.glob("*.sql"))

    print(f"  📄 CSV 文件: {len(csv_files)} 个")
    for f in csv_files:
        print(f"     - {f.name}")

    if sql_files:
        print(f"  📄 SQL 初始化脚本: {sql_files[0].name}")

    print()
    return True


def check_mysql_data():
    """尝试检查 MySQL 是否有示例数据"""
    print("🔍 检查 MySQL 数据...")

    try:
        import pymysql
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "enterprise_db"),
            connect_timeout=3,
        )
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            if tables:
                print(f"  ✅ 数据库中已有 {len(tables)} 张表:")
                for t in tables:
                    table_name = list(t.values())[0]
                    cursor.execute(f"SELECT COUNT(*) AS cnt FROM `{table_name}`")
                    count = cursor.fetchone()["cnt"]
                    print(f"     - {table_name}: {count} 条记录")
            else:
                print("  ⚠️  数据库为空，请执行初始化脚本:")
                print(f"     mysql -u root -p < sample_data/init_mysql.sql")
        conn.close()
    except Exception as e:
        print(f"  ⚠️  MySQL 连接失败: {e}")
        print("     CSV 数据源仍可正常使用")

    print()


def main():
    """主启动流程"""
    print_banner()

    # 1. 检查依赖
    if not check_dependencies():
        sys.exit(1)

    # 2. 检查环境
    if not check_env():
        sys.exit(1)

    # 3. 检查数据
    check_sample_data()
    check_mysql_data()

    # 4. 启动应用
    print("🚀 正在启动智能助理...\n")
    print("=" * 60)

    from enterprise_assistant.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
