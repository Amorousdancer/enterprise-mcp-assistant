#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Redis MCP Server — 企业级键值存储访问服务

通过 MCP 协议暴露 Redis 数据库的查询能力，内置危险命令拦截、
Key 命名空间管理、值大小限制等安全特性。

启动方式: python mcp_servers/redis_server.py
"""

import os
import json
import redis
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# MCP Server 实例
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "Redis-Enterprise",
    instructions="企业级 Redis 键值存储访问服务。提供安全的 key 查询、hash 读取、服务器信息等功能。"
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", "6379")),
    "db": int(os.getenv("REDIS_DB", "0")),
    "password": os.getenv("REDIS_PASSWORD", None),
    "decode_responses": True,
    "socket_connect_timeout": 5,
    "socket_timeout": 5,
}

# 安全常量
MAX_KEYS = 100           # KEWS 命令最多返回 key 数
MAX_VALUE_LENGTH = 10240  # 单个 value 最大字节数 (10KB)
BLOCKED_COMMANDS = {"FLUSHALL", "FLUSHDB", "SHUTDOWN", "DEBUG", "CONFIG"}

# 安全的 SCAN 匹配模式限制
MAX_PATTERN_LENGTH = 100


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _get_client() -> redis.Redis:
    """获取 Redis 客户端连接"""
    return redis.Redis(**REDIS_CONFIG)


def _check_connection() -> tuple[bool, str]:
    """检查 Redis 连接状态"""
    try:
        client = _get_client()
        info = client.info("server")
        return True, f"Redis {info.get('redis_version', 'unknown')}"
    except Exception as e:
        return False, str(e)


def _truncate_value(value: str) -> tuple[str, bool]:
    """截断过长的 value"""
    if len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + f"\n... [截断，原始长度 {len(value)} 字节]", True
    return value, False


# ---------------------------------------------------------------------------
# MCP 工具定义
# ---------------------------------------------------------------------------

@mcp.tool()
def redis_get(key: str) -> str:
    """
    获取指定 key 的值。如果 key 是 hash/list/set/zset，自动转换为可读格式。

    Args:
        key: Redis key 名称

    Returns:
        key 的值和类型信息的 JSON 字符串。
    """
    try:
        client = _get_client()
        key_type = client.type(key)

        if key_type == "none":
            return json.dumps({"error": f"Key '{key}' 不存在"}, ensure_ascii=False)

        result = {"key": key, "type": key_type}

        if key_type == "string":
            value = client.get(key)
            value, truncated = _truncate_value(value)
            result["value"] = value
            if truncated:
                result["warning"] = "值已截断"

        elif key_type == "hash":
            result["value"] = client.hgetall(key)

        elif key_type == "list":
            length = client.llen(key)
            result["value"] = client.lrange(key, 0, MAX_KEYS - 1)
            result["length"] = length
            if length > MAX_KEYS:
                result["warning"] = f"列表共 {length} 个元素，仅返回前 {MAX_KEYS} 个"

        elif key_type == "set":
            members = client.smembers(key)
            result["value"] = list(members)[:MAX_KEYS]
            result["size"] = len(members)

        elif key_type == "zset":
            result["value"] = client.zrange(key, 0, MAX_KEYS - 1, withscores=True)
            result["size"] = client.zcard(key)

        else:
            result["value"] = f"不支持的类型: {key_type}"

        # 设置 TTL 信息
        ttl = client.ttl(key)
        if ttl > 0:
            result["ttl_seconds"] = ttl
        elif ttl == -1:
            result["ttl"] = "永久"

        return json.dumps(result, ensure_ascii=False, default=str)

    except redis.RedisError as e:
        return json.dumps({"error": f"Redis 错误: {e}"}, ensure_ascii=False)


@mcp.tool()
def redis_set(key: str, value: str, expire_seconds: int = 0) -> str:
    """
    设置一个 string 类型的 key-value。

    Args:
        key: Redis key 名称
        value: 值
        expire_seconds: 过期时间（秒），0 表示不过期

    Returns:
        操作结果的 JSON 字符串。
    """
    try:
        client = _get_client()
        if expire_seconds > 0:
            client.setex(key, expire_seconds, value)
        else:
            client.set(key, value)

        return json.dumps({
            "success": True,
            "key": key,
            "ttl": expire_seconds if expire_seconds > 0 else "permanent",
        }, ensure_ascii=False)

    except redis.RedisError as e:
        return json.dumps({"error": f"Redis 错误: {e}"}, ensure_ascii=False)


@mcp.tool()
def redis_keys(pattern: str = "*") -> str:
    """
    按模式搜索 Redis key（使用 SCAN 命令，安全友好）。

    Args:
        pattern: 匹配模式（如 "user:*", "session:123*"），默认 "*"

    Returns:
        匹配的 key 列表的 JSON 字符串。
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        return json.dumps({"error": "匹配模式过长"}, ensure_ascii=False)

    try:
        client = _get_client()
        keys = []
        cursor = 0
        while True:
            cursor, batch = client.scan(cursor, match=pattern, count=50)
            keys.extend(batch)
            if len(keys) >= MAX_KEYS:
                keys = keys[:MAX_KEYS]
                break
            if cursor == 0:
                break

        return json.dumps({
            "pattern": pattern,
            "count": len(keys),
            "keys": keys,
            "truncated": len(keys) >= MAX_KEYS,
        }, ensure_ascii=False)

    except redis.RedisError as e:
        return json.dumps({"error": f"Redis 错误: {e}"}, ensure_ascii=False)


@mcp.tool()
def redis_hgetall(key: str) -> str:
    """
    获取 Hash 类型 key 的所有字段和值。

    Args:
        key: Redis key 名称

    Returns:
        Hash 所有字段的 JSON 字符串。
    """
    try:
        client = _get_client()
        key_type = client.type(key)

        if key_type != "hash":
            return json.dumps({
                "error": f"Key '{key}' 的类型是 {key_type}，不是 hash"
            }, ensure_ascii=False)

        data = client.hgetall(key)
        return json.dumps({
            "key": key,
            "field_count": len(data),
            "data": data,
        }, ensure_ascii=False)

    except redis.RedisError as e:
        return json.dumps({"error": f"Redis 错误: {e}"}, ensure_ascii=False)


@mcp.tool()
def redis_type(key: str) -> str:
    """
    查看指定 key 的数据类型和 TTL 信息。

    Args:
        key: Redis key 名称

    Returns:
        key 类型信息的 JSON 字符串。
    """
    try:
        client = _get_client()
        key_type = client.type(key)
        ttl = client.ttl(key)
        exists = client.exists(key)

        result = {
            "key": key,
            "exists": bool(exists),
            "type": key_type,
        }

        if ttl > 0:
            result["ttl_seconds"] = ttl
        elif ttl == -1:
            result["ttl"] = "永久"
        elif ttl == -2:
            result["ttl"] = "已过期/不存在"

        return json.dumps(result, ensure_ascii=False)

    except redis.RedisError as e:
        return json.dumps({"error": f"Redis 错误: {e}"}, ensure_ascii=False)


@mcp.tool()
def redis_info() -> str:
    """
    获取 Redis 服务器的基本信息（版本、内存、客户端数、key 数量等）。

    Returns:
        Redis 服务器信息的 JSON 字符串。
    """
    try:
        client = _get_client()
        info = client.info()

        # 只返回关键信息，不暴露所有字段
        safe_info = {
            "redis_version": info.get("redis_version"),
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
            "uptime_in_days": info.get("uptime_in_days"),
            "total_commands_processed": info.get("total_commands_processed"),
            "keyspace": {},
        }

        # 统计各 DB 的 key 数
        for key, value in info.items():
            if key.startswith("db"):
                safe_info["keyspace"][key] = value

        return json.dumps(safe_info, ensure_ascii=False)

    except redis.RedisError as e:
        return json.dumps({"error": f"Redis 错误: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run()
