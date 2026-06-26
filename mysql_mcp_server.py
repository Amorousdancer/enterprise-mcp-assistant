"""
MySQL MCP Server — 基于官方 MCP SDK 的安全 MySQL 数据库访问服务

功能:
  - get_db_schema: 获取数据库表结构
  - execute_safe_query: 执行只读 SQL 查询（含注入防御 + 敏感字段脱敏）

环境变量:
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""

import json
import os
import re

import mysql.connector
from mysql.connector import pooling
import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import Keyword, DML

from mcp.server.fastmcp import FastMCP

# ============================================================
# 配置
# ============================================================

MAX_ROW_LIMIT = 1000
DEFAULT_ROW_LIMIT = 100
QUERY_TIMEOUT_SECONDS = 10

# 危险关键词（转大写后匹配）
BLOCKED_KEYWORDS = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "REPLACE", "RENAME",
    "EXEC", "EXECUTE", "CALL",
    # "INTO" 不加入此处，由专项检测处理 INTO OUTFILE/DUMPFILE，避免误报
    "LOAD",        # LOAD_FILE / LOAD DATA
    "SLEEP",       # 时间盲注
    "BENCHMARK",   # 时间盲注
}

# 允许的语句类型（sqlparse 解析后的 first token）
ALLOWED_STATEMENT_TYPES = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}

# 敏感字段名模式（小写匹配）
SENSITIVE_PATTERNS = {
    "password":  "full",      # ***...***
    "passwd":    "full",
    "secret":    "full",
    "token":     "full",
    "api_key":   "full",
    "apikey":    "full",
    "phone":     "phone",     # 138****1234
    "mobile":    "phone",
    "email":     "email",     # u***@domain.com
    "id_card":   "id_card",   # 前4后4
    "idcard":    "id_card",
    "ssn":       "id_card",
    "credit_card": "id_card",
    "card_no":   "id_card",
    "bank_card": "id_card",
}

# ============================================================
# 脱敏工具函数
# ============================================================


def mask_value(value: str, mask_type: str) -> str:
    """对敏感值进行脱敏处理"""
    if value is None:
        return None
    s = str(value)
    if not s:
        return s

    if mask_type == "full":
        # 完全遮蔽
        return "***"

    if mask_type == "phone":
        # 手机号: 保留前3后4
        if len(s) >= 7:
            return s[:3] + "****" + s[-4:]
        return "****"

    if mask_type == "email":
        # 邮箱: 保留首字符和域名
        parts = s.split("@")
        if len(parts) == 2:
            local = parts[0]
            domain = parts[1]
            prefix = local[0] if local else "*"
            return f"{prefix}***@{domain}"
        return "***"

    if mask_type == "id_card":
        # 身份证/银行卡: 保留前4后4
        if len(s) >= 8:
            return s[:4] + "*" * (len(s) - 8) + s[-4:]
        return "****"

    return "***"


def mask_rows(columns: list[str], rows: list[tuple]) -> list[tuple]:
    """对结果集中的敏感字段进行脱敏"""
    # 识别哪些列需要脱敏以及类型
    col_mask_map: dict[int, str] = {}
    for idx, col_name in enumerate(columns):
        col_lower = col_name.lower().strip("`\"' ")
        for pattern, mask_type in SENSITIVE_PATTERNS.items():
            if pattern in col_lower:
                col_mask_map[idx] = mask_type
                break

    if not col_mask_map:
        return rows

    masked = []
    for row in rows:
        new_row = list(row)
        for idx, mask_type in col_mask_map.items():
            if idx < len(new_row) and new_row[idx] is not None:
                new_row[idx] = mask_value(new_row[idx], mask_type)
        masked.append(tuple(new_row))
    return masked


# ============================================================
# SQL 安全校验
# ============================================================


class SQLInjectionError(Exception):
    """SQL 安全校验失败"""
    pass


def strip_sql_comments(sql: str) -> str:
    """去除 SQL 注释，防止基于注释的注入"""
    # 去除 -- 单行注释
    sql = re.sub(r'--[^\n]*', '', sql)
    # 去除 # 单行注释
    sql = re.sub(r'#[^\n]*', '', sql)
    # 去除 /* ... */ 块注释
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    return sql.strip()


def validate_sql(sql: str) -> str:
    """
    对 SQL 进行安全校验，返回清理后的 SQL。
    不通过则抛出 SQLInjectionError。
    """
    if not sql or not sql.strip():
        raise SQLInjectionError("SQL 语句不能为空")

    # 1) 去除注释
    cleaned = strip_sql_comments(sql)
    if not cleaned:
        raise SQLInjectionError("SQL 语句在去除注释后为空")

    # 2) 检测多语句（分号分割，但排除末尾分号）
    stripped = cleaned.rstrip(";").strip()
    # 使用 sqlparse 分割多语句
    statements = sqlparse.split(stripped)
    statements = [s.strip() for s in statements if s.strip()]
    if len(statements) > 1:
        raise SQLInjectionError("不允许执行多条 SQL 语句")

    # 3) 解析语句类型
    parsed = sqlparse.parse(stripped)[0]
    stmt_type = parsed.get_type()  # SELECT / INSERT / ... 或 None

    # 对于 SHOW / DESCRIBE / EXPLAIN，sqlparse 可能返回 None
    # 需要手动检查 first token
    first_token_upper = None
    for token in parsed.tokens:
        if token.ttype in (sqlparse.tokens.Keyword, sqlparse.tokens.Keyword.DDL,
                           sqlparse.tokens.Keyword.DML) or token.is_keyword:
            first_token_upper = token.normalized.upper()
            break
        if token.ttype is sqlparse.tokens.Keyword:
            first_token_upper = token.normalized.upper()
            break

    # 尝试从 token 列表中获取首个有效关键词
    if first_token_upper is None:
        for token in parsed.flatten():
            if token.ttype in (Keyword, DML, Keyword.DDL, Keyword.DML):
                first_token_upper = token.normalized.upper()
                break

    # 兜底：直接从原始文本取第一个词
    if first_token_upper is None:
        first_word = stripped.split()[0].upper() if stripped.split() else ""
        first_token_upper = first_word

    if first_token_upper not in ALLOWED_STATEMENT_TYPES:
        raise SQLInjectionError(
            f"不允许的语句类型: {first_token_upper}。"
            f"仅允许: {', '.join(sorted(ALLOWED_STATEMENT_TYPES))}"
        )

    # 4) 逐 token 检查危险关键词
    sql_upper = stripped.upper()
    # 排除字符串字面量中的内容：粗略移除引号内字符串
    sql_no_strings = re.sub(r"'[^']*'", "''", sql_upper)
    sql_no_strings = re.sub(r'"[^"]*"', '""', sql_no_strings)

    for keyword in BLOCKED_KEYWORDS:
        # 使用词边界匹配，避免误匹配（如 "UPDATED_AT" 中的 "UPDATE"）
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, sql_no_strings):
            raise SQLInjectionError(f"检测到危险关键词: {keyword}")

    # 5) 检测可疑的 UNION 注入
    if re.search(r'\bUNION\b', sql_no_strings):
        raise SQLInjectionError("检测到 UNION 关键词，可能存在注入风险")

    # 6) 检测 INTO OUTFILE / INTO DUMPFILE
    if re.search(r'\bINTO\s+(OUTFILE|DUMPFILE)\b', sql_no_strings):
        raise SQLInjectionError("检测到 INTO OUTFILE/DUMPFILE，不允许写文件操作")

    # 7) 检测 LOAD_FILE
    if re.search(r'\bLOAD_FILE\s*\(', sql_no_strings):
        raise SQLInjectionError("检测到 LOAD_FILE 函数调用")

    # 8) 检测系统函数调用
    dangerous_funcs = [
        "VERSION", "DATABASE", "USER", "CURRENT_USER", "SESSION_USER",
        "SYSTEM_USER", "FOUND_ROWS", "ROW_COUNT", "UUID",
    ]
    for func in dangerous_funcs:
        if re.search(r'\b' + func + r'\s*\(', sql_no_strings):
            raise SQLInjectionError(f"检测到系统函数调用: {func}()")

    return stripped


def ensure_limit(sql: str, user_limit: int) -> str:
    """确保 SELECT 语句包含 LIMIT 子句"""
    sql_upper = sql.upper().strip()
    if sql_upper.startswith("SELECT") and "LIMIT" not in sql_upper:
        limit = min(user_limit, MAX_ROW_LIMIT)
        sql = sql.rstrip(";") + f" LIMIT {limit}"
    return sql


# ============================================================
# MySQL 连接池
# ============================================================

_pool: pooling.MySQLConnectionPool | None = None


def get_pool() -> pooling.MySQLConnectionPool:
    """获取或创建 MySQL 连接池"""
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="mcp_mysql_pool",
            pool_size=5,
            host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ.get("MYSQL_USER", "root"),
            password=os.environ.get("MYSQL_PASSWORD", ""),
            database=os.environ.get("MYSQL_DATABASE", ""),
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            autocommit=True,
            connection_timeout=QUERY_TIMEOUT_SECONDS,
        )
    return _pool


def execute_query(sql: str, params: tuple | None = None) -> tuple[list[str], list[tuple]]:
    """执行 SQL 并返回 (columns, rows)"""
    conn = get_pool().get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall() if columns else []
        cursor.close()
        return columns, rows
    finally:
        conn.close()


# ============================================================
# MCP Server
# ============================================================

mcp = FastMCP(
    "MySQL MCP Server",
    description="安全连接本地 MySQL 数据库，提供表结构查询和只读 SQL 执行",
)


@mcp.tool()
def get_db_schema(table_name: str = "") -> str:
    """
    获取数据库表结构信息。

    Args:
        table_name: 表名。为空时返回所有表的列表；非空时返回该表的详细结构。

    Returns:
        JSON 格式的表结构信息。
    """
    try:
        if not table_name:
            # 返回所有表
            columns, rows = execute_query("SHOW TABLES")
            tables = [row[0] for row in rows]
            return json.dumps({
                "status": "success",
                "database": os.environ.get("MYSQL_DATABASE", ""),
                "table_count": len(tables),
                "tables": tables,
            }, ensure_ascii=False, indent=2)

        # 表名安全检查：只允许字母、数字、下划线
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
            return json.dumps({
                "status": "error",
                "message": f"表名不合法: {table_name}。表名只能包含字母、数字和下划线。",
            }, ensure_ascii=False)

        # 获取表结构
        columns, rows = execute_query(f"DESCRIBE `{table_name}`")
        col_info = []
        for row in rows:
            col_info.append({
                "field": row[0],
                "type": str(row[1]),
                "null": row[2],
                "key": row[3],
                "default": row[4],
                "extra": row[5],
            })

        # 获取建表语句（含注释）
        try:
            _, create_rows = execute_query(f"SHOW CREATE TABLE `{table_name}`")
            create_stmt = create_rows[0][1] if create_rows else None
        except Exception:
            create_stmt = None

        # 获取行数估算
        try:
            _, count_rows = execute_query(
                "SELECT TABLE_ROWS FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (table_name,)
            )
            estimated_rows = count_rows[0][0] if count_rows else None
        except Exception:
            estimated_rows = None

        return json.dumps({
            "status": "success",
            "table": table_name,
            "estimated_rows": estimated_rows,
            "columns": col_info,
            "create_statement": create_stmt,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        }, ensure_ascii=False)


@mcp.tool()
def execute_safe_query(sql: str, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """
    执行只读 SQL 查询，自动进行安全校验和敏感字段脱敏。

    安全保障:
      - 仅允许 SELECT / SHOW / DESCRIBE / EXPLAIN 语句
      - 自动拦截 SQL 注入（危险关键词、多语句、UNION 等）
      - 强制行数限制，防止大结果集
      - 自动对敏感字段（密码、手机号、邮箱等）进行脱敏

    Args:
        sql: 要执行的 SQL 查询语句。
        limit: 最大返回行数，默认 100，上限 1000。

    Returns:
        JSON 格式的查询结果（含列名和数据行，敏感字段已脱敏）。
    """
    try:
        # 1) 安全校验
        validated_sql = validate_sql(sql)

        # 2) 强制限制行数
        limit = max(1, min(limit, MAX_ROW_LIMIT))
        final_sql = ensure_limit(validated_sql, limit)

        # 3) 执行查询
        columns, rows = execute_query(final_sql)

        # 4) 敏感字段脱敏
        rows = mask_rows(columns, rows)

        # 5) 构造返回结果
        data = []
        for row in rows:
            row_dict = {}
            for i, col in enumerate(columns):
                val = row[i] if i < len(row) else None
                # JSON 序列化特殊类型
                if val is not None and not isinstance(val, (str, int, float, bool)):
                    val = str(val)
                row_dict[col] = val
            data.append(row_dict)

        return json.dumps({
            "status": "success",
            "query": final_sql,
            "column_count": len(columns),
            "row_count": len(data),
            "columns": columns,
            "rows": data,
        }, ensure_ascii=False, indent=2)

    except SQLInjectionError as e:
        return json.dumps({
            "status": "blocked",
            "reason": str(e),
            "original_sql": sql,
        }, ensure_ascii=False)

    except mysql.connector.Error as e:
        return json.dumps({
            "status": "error",
            "message": f"MySQL 错误: {e.msg}",
            "error_code": e.errno,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
        }, ensure_ascii=False)


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
