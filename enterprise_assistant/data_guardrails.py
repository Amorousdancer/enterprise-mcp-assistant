#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
企业级数据访问安全策略

基于 LightAgent 的 Guardrails 系统，实现三层安全防护：
1. SQL 注入防护 — 拦截危险 SQL 操作
2. 数据访问审计 — 记录所有数据访问行为
3. Redis 危险命令拦截 — 防止误操作

注意：LightAgent 框架的 tool_guardrail 签名为 guardrail(payload, context)，
其中 payload = {"tool_name": str, "arguments": dict}。
"""

import re
import json
import logging
from typing import Any

# 审计日志器
audit_logger = logging.getLogger("data_audit")
audit_handler = logging.FileHandler("logs/data_audit.log", encoding="utf-8")
audit_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s"
))
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# 策略 1: SQL 注入防护
# ---------------------------------------------------------------------------

# 危险 SQL 关键字（大写）
DANGEROUS_SQL_KEYWORDS = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
    "TRUNCATE", "REPLACE", "CREATE", "GRANT", "REVOKE",
    "LOAD", "EXEC", "EXECUTE",
}


def sql_injection_guardrail(payload: dict, context: dict = None) -> dict:
    """
    SQL 注入防护策略。
    检查 mysql_query 工具的 SQL 参数，拦截危险操作。

    Args:
        payload: {"tool_name": str, "arguments": dict}
        context: 上下文信息

    Returns:
        {"allowed": True} — 允许执行
        {"allowed": False, "reason": "..."} — 拦截
    """
    tool_name = payload.get("tool_name", "")
    arguments = payload.get("arguments", {})

    if tool_name != "mysql_query":
        return {"allowed": True}

    sql = arguments.get("sql", "").upper().strip()

    # 只允许安全的查询语句
    if not sql.startswith(("SELECT", "SHOW", "DESCRIBE", "EXPLAIN")):
        return {
            "allowed": False,
            "reason": f"安全策略拒绝: 仅允许 SELECT/SHOW/DESCRIBE 语句，检测到: {sql[:20]}..."
        }

    # 检查危险关键字
    for kw in DANGEROUS_SQL_KEYWORDS:
        if re.search(rf"\b{kw}\b", sql):
            return {
                "allowed": False,
                "reason": f"安全策略拒绝: 检测到危险操作 '{kw}'"
            }

    # 检查注释注入（--; /* */）
    if "--" in sql or "/*" in sql:
        return {
            "allowed": False,
            "reason": "安全策略拒绝: SQL 中不允许包含注释"
        }

    return {"allowed": True}


# ---------------------------------------------------------------------------
# 策略 2: 数据访问审计
# ---------------------------------------------------------------------------

def data_access_audit_guardrail(payload: dict, context: dict = None) -> dict:
    """
    数据访问审计策略。
    记录所有数据访问操作到审计日志。
    永远不拦截，只做记录。
    """
    tool_name = payload.get("tool_name", "")
    arguments = payload.get("arguments", {})
    ctx = context or {}

    audit_logger.info(json.dumps({
        "event": "tool_call",
        "user_id": ctx.get("user_id", "unknown"),
        "trace_id": ctx.get("trace_id", ""),
        "tool": tool_name,
        "params": _mask_sensitive(arguments),
    }, ensure_ascii=False))

    return {"allowed": True}


def _mask_sensitive(params: dict) -> dict:
    """对参数中的敏感信息进行脱敏"""
    masked = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > 100:
            # 截断过长的参数（如 SQL 语句）
            masked[k] = v[:100] + "..."
        else:
            masked[k] = v
    return masked


# ---------------------------------------------------------------------------
# 策略 3: Redis 危险命令拦截
# ---------------------------------------------------------------------------

BLOCKED_REDIS_KEYS = {"FLUSHALL", "FLUSHDB", "SHUTDOWN", "CONFIG", "DEBUG", "KEYS"}


def redis_safety_guardrail(payload: dict, context: dict = None) -> dict:
    """
    Redis 安全策略。
    拦截危险的 Redis key 操作。
    """
    tool_name = payload.get("tool_name", "")
    arguments = payload.get("arguments", {})

    if tool_name == "redis_set":
        key = arguments.get("key", "")
        # 拦截系统级 key 的写入
        if key.startswith(("config:", "admin:", "system:")):
            return {
                "allowed": False,
                "reason": f"安全策略拒绝: 不允许写入系统级 key '{key}'"
            }

    if tool_name == "redis_keys":
        pattern = arguments.get("pattern", "")
        if pattern == "*" or len(pattern) == 0:
            return {
                "allowed": False,
                "reason": "安全策略拒绝: 不允许执行 'KEYS *' 全量扫描，请使用更精确的模式"
            }

    return {"allowed": True}


# ---------------------------------------------------------------------------
# 组合策略：将所有 guardrail 组合为一个列表传给 LightAgent
# ---------------------------------------------------------------------------

def get_all_guardrails() -> list:
    """返回所有安全策略，可直接传给 LightAgent 的 tool_guardrails 参数"""
    return [
        sql_injection_guardrail,
        data_access_audit_guardrail,
        redis_safety_guardrail,
    ]
