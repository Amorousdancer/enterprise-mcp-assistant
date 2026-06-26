#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
安全模块单元测试

覆盖：
1. SQL 注入防御 (mysql_mcp_server.validate_sql)
2. 敏感字段脱敏 (mysql_mcp_server.mask_value / mask_rows)
3. 安全护栏函数签名兼容性 (enterprise_assistant.data_guardrails)
4. CSV 安全表达式求值 (mcp_servers.csv_server._safe_eval)
"""

import ast
import pytest
import sys
import os

# 将项目根目录加入 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===================================================================
# 1. SQL 注入防御测试
# ===================================================================

from mysql_mcp_server import validate_sql, SQLInjectionError, mask_value, mask_rows


class TestSQLInjectionDefense:
    """测试 validate_sql 的 5 层注入防御"""

    # --- 正常查询应通过 ---
    def test_valid_select(self):
        result = validate_sql("SELECT * FROM employees")
        assert result.upper().startswith("SELECT")

    def test_valid_show(self):
        result = validate_sql("SHOW TABLES")
        assert "SHOW" in result.upper()

    def test_valid_describe(self):
        result = validate_sql("DESCRIBE employees")
        assert "DESCRIBE" in result.upper()

    def test_valid_explain(self):
        result = validate_sql("EXPLAIN SELECT * FROM employees")
        assert "EXPLAIN" in result.upper()

    # --- 第 1 层：语句类型白名单 ---
    def test_block_insert(self):
        with pytest.raises(SQLInjectionError, match="不允许的语句类型"):
            validate_sql("INSERT INTO employees VALUES (1, 'test')")

    def test_block_delete(self):
        with pytest.raises(SQLInjectionError, match="不允许的语句类型"):
            validate_sql("DELETE FROM employees WHERE id = 1")

    def test_block_update(self):
        with pytest.raises(SQLInjectionError, match="不允许的语句类型"):
            validate_sql("UPDATE employees SET salary = 0")

    def test_block_drop(self):
        with pytest.raises(SQLInjectionError, match="不允许的语句类型"):
            validate_sql("DROP TABLE employees")

    def test_block_create(self):
        with pytest.raises(SQLInjectionError, match="不允许的语句类型"):
            validate_sql("CREATE TABLE hack (id INT)")

    def test_block_alter(self):
        with pytest.raises(SQLInjectionError, match="不允许的语句类型"):
            validate_sql("ALTER TABLE employees ADD COLUMN hack TEXT")

    # --- 第 2 层：危险关键词检测 ---
    def test_block_sleep_injection(self):
        with pytest.raises(SQLInjectionError, match="危险关键词"):
            validate_sql("SELECT * FROM employees WHERE id = 1 AND SLEEP(5)")

    def test_block_benchmark_injection(self):
        with pytest.raises(SQLInjectionError, match="危险关键词"):
            validate_sql("SELECT * FROM employees WHERE id = 1 AND BENCHMARK(1000000, SHA1('test'))")

    def test_block_load_file(self):
        with pytest.raises(SQLInjectionError, match="LOAD_FILE"):
            validate_sql("SELECT LOAD_FILE('/etc/passwd')")

    # --- 第 3 层：多语句检测 ---
    def test_block_multi_statement(self):
        with pytest.raises(SQLInjectionError, match="多条 SQL"):
            validate_sql("SELECT * FROM employees; DROP TABLE employees")

    def test_block_multi_statement_with_semicolon(self):
        with pytest.raises(SQLInjectionError, match="多条 SQL"):
            validate_sql("SELECT 1; SELECT 2")

    # --- 第 4 层：注释去除 ---
    def test_comment_stripping_removes_danger(self):
        """注释后跟危险语句 — 注释去除后变安全"""
        # "-- ; DROP TABLE employees" 被当作注释去除，剩下安全的 SELECT
        result = validate_sql("SELECT * FROM employees -- ; DROP TABLE employees")
        assert "DROP" not in result.upper()

    def test_block_comment_stripping(self):
        """块注释注入 — 注释去除后变安全"""
        result = validate_sql("SELECT * FROM employees /* ; DROP TABLE employees */")
        assert "DROP" not in result.upper()

    def test_empty_after_comment_stripped(self):
        """去除注释后为空"""
        with pytest.raises(SQLInjectionError, match="去除注释后为空"):
            validate_sql("-- this is just a comment")

    # --- 第 5 层：UNION 注入 ---
    def test_block_union_injection(self):
        with pytest.raises(SQLInjectionError, match="UNION"):
            validate_sql("SELECT * FROM employees WHERE id = 1 UNION SELECT * FROM admin")

    def test_block_union_all_injection(self):
        with pytest.raises(SQLInjectionError, match="UNION"):
            validate_sql("SELECT name FROM employees UNION ALL SELECT password FROM users")

    # --- INTO OUTFILE ---
    def test_block_into_outfile(self):
        with pytest.raises(SQLInjectionError, match="INTO OUTFILE"):
            validate_sql("SELECT * INTO OUTFILE '/tmp/hack.txt' FROM employees")

    # --- 空 SQL ---
    def test_empty_sql(self):
        with pytest.raises(SQLInjectionError, match="不能为空"):
            validate_sql("")

    def test_none_sql(self):
        with pytest.raises(SQLInjectionError, match="不能为空"):
            validate_sql(None)

    def test_whitespace_only(self):
        with pytest.raises(SQLInjectionError, match="不能为空"):
            validate_sql("   ")

    # --- 边界：列名包含 UPDATE 子串不应误报 ---
    def test_column_name_substring_not_false_positive(self):
        """列名 updated_at 不应被 UPDATE 关键词误匹配"""
        result = validate_sql("SELECT updated_at FROM employees")
        assert "updated_at" in result.lower()


# ===================================================================
# 2. 敏感字段脱敏测试
# ===================================================================

class TestSensitiveDataMasking:
    """测试 mask_value 和 mask_rows 的脱敏逻辑"""

    # --- mask_value ---
    def test_phone_masking(self):
        assert mask_value("13812345678", "phone") == "138****5678"

    def test_phone_short(self):
        assert mask_value("123", "phone") == "****"

    def test_email_masking(self):
        assert mask_value("user@example.com", "email") == "u***@example.com"

    def test_email_masking_no_at(self):
        assert mask_value("invalid-email", "email") == "***"

    def test_id_card_masking(self):
        assert mask_value("110101199001011234", "id_card") == "1101**********1234"

    def test_id_card_short(self):
        assert mask_value("12345", "id_card") == "****"

    def test_full_masking(self):
        assert mask_value("supersecret", "full") == "***"

    def test_none_value(self):
        assert mask_value(None, "phone") is None

    def test_empty_string(self):
        assert mask_value("", "phone") == ""

    # --- mask_rows ---
    def test_mask_rows_phone_column(self):
        columns = ["name", "phone", "city"]
        rows = [
            ("张三", "13812345678", "上海"),
            ("李四", "15098765432", "北京"),
        ]
        masked = mask_rows(columns, rows)
        assert masked[0][1] == "138****5678"
        assert masked[1][1] == "150****5432"
        # 非敏感列不变
        assert masked[0][0] == "张三"
        assert masked[0][2] == "上海"

    def test_mask_rows_email_column(self):
        columns = ["name", "email"]
        rows = [("张三", "zhangsan@qq.com")]
        masked = mask_rows(columns, rows)
        assert masked[0][1] == "z***@qq.com"

    def test_mask_rows_password_column(self):
        columns = ["username", "password"]
        rows = [("admin", "hashed_secret_value")]
        masked = mask_rows(columns, rows)
        assert masked[0][1] == "***"

    def test_mask_rows_id_card_column(self):
        columns = ["name", "id_card"]
        rows = [("张三", "110101199001011234")]
        masked = mask_rows(columns, rows)
        assert masked[0][1] == "1101**********1234"

    def test_mask_rows_no_sensitive_columns(self):
        columns = ["name", "age", "city"]
        rows = [("张三", 30, "上海")]
        masked = mask_rows(columns, rows)
        # 无敏感列时原样返回
        assert masked == rows

    def test_mask_rows_multiple_sensitive_columns(self):
        columns = ["name", "phone", "email", "password"]
        rows = [("张三", "13812345678", "z@qq.com", "secret123")]
        masked = mask_rows(columns, rows)
        assert masked[0][0] == "张三"  # 非敏感
        assert masked[0][1] == "138****5678"  # phone
        assert masked[0][2] == "z***@qq.com"  # email
        assert masked[0][3] == "***"  # password

    def test_mask_rows_none_value_skipped(self):
        """None 值不应被脱敏"""
        columns = ["name", "phone"]
        rows = [("张三", None)]
        masked = mask_rows(columns, rows)
        assert masked[0][1] is None


# ===================================================================
# 3. 安全护栏函数签名兼容性测试
# ===================================================================

from enterprise_assistant.data_guardrails import (
    sql_injection_guardrail,
    redis_safety_guardrail,
    data_access_audit_guardrail,
)


class TestGuardrailSignatureCompatibility:
    """确保 guardrail 函数与 LightAgent 框架的调用方式兼容"""

    def test_sql_guardrail_allows_safe_query(self):
        payload = {"tool_name": "mysql_query", "arguments": {"sql": "SELECT * FROM employees"}}
        result = sql_injection_guardrail(payload, {})
        assert result.get("allowed") is True

    def test_sql_guardrail_blocks_delete(self):
        payload = {"tool_name": "mysql_query", "arguments": {"sql": "DELETE FROM employees"}}
        result = sql_injection_guardrail(payload, {})
        assert result.get("allowed") is False
        assert "DELETE" in result.get("reason", "")

    def test_sql_guardrail_blocks_drop(self):
        payload = {"tool_name": "mysql_query", "arguments": {"sql": "DROP TABLE employees"}}
        result = sql_injection_guardrail(payload, {})
        assert result.get("allowed") is False

    def test_sql_guardrail_ignores_other_tools(self):
        payload = {"tool_name": "csv_query", "arguments": {"expression": "df.head()"}}
        result = sql_injection_guardrail(payload, {})
        assert result.get("allowed") is True

    def test_sql_guardrail_blocks_comment_injection(self):
        payload = {"tool_name": "mysql_query", "arguments": {"sql": "SELECT * -- ; DROP TABLE t"}}
        result = sql_injection_guardrail(payload, {})
        assert result.get("allowed") is False

    def test_redis_guardrail_blocks_system_key(self):
        payload = {"tool_name": "redis_set", "arguments": {"key": "config:dangerous", "value": "x"}}
        result = redis_safety_guardrail(payload, {})
        assert result.get("allowed") is False

    def test_redis_guardrail_blocks_keys_star(self):
        payload = {"tool_name": "redis_keys", "arguments": {"pattern": "*"}}
        result = redis_safety_guardrail(payload, {})
        assert result.get("allowed") is False

    def test_redis_guardrail_allows_normal_key(self):
        payload = {"tool_name": "redis_set", "arguments": {"key": "user:1:name", "value": "张三"}}
        result = redis_safety_guardrail(payload, {})
        assert result.get("allowed") is True

    def test_audit_guardrail_always_allows(self):
        payload = {"tool_name": "mysql_query", "arguments": {"sql": "SELECT 1"}}
        result = data_access_audit_guardrail(payload, {"user_id": "test"})
        assert result.get("allowed") is True

    def test_guardrail_returns_dict_not_guardrail_decision(self):
        """确保返回的是 dict（框架的 _coerce_decision 会处理）"""
        payload = {"tool_name": "mysql_query", "arguments": {"sql": "SELECT 1"}}
        result = sql_injection_guardrail(payload, {})
        assert isinstance(result, dict)
        assert "allowed" in result


# ===================================================================
# 4. CSV 安全表达式求值测试
# ===================================================================

import pandas as pd

# 导入安全求值器
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_servers"))
from csv_server import _safe_eval, _validate_expression


class TestCSVSafeEval:
    """测试 AST 安全求值器"""

    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame({
            "name": ["张三", "李四", "王五", "赵六"],
            "age": [30, 25, 35, 28],
            "department": ["技术部", "财务部", "技术部", "市场部"],
            "salary": [15000, 12000, 18000, 10000],
        })

    def _eval(self, expr: str, df: pd.DataFrame):
        tree = ast.parse(expr, mode="eval")
        return _safe_eval(tree.body, df)

    # --- 基本过滤 ---
    def test_column_access(self, sample_df):
        result = self._eval("df['name']", sample_df)
        assert list(result) == ["张三", "李四", "王五", "赵六"]

    def test_boolean_filter(self, sample_df):
        result = self._eval("df[df['age'] > 30]", sample_df)
        assert len(result) == 1
        assert result.iloc[0]["name"] == "王五"

    def test_multi_condition_filter(self, sample_df):
        result = self._eval("df[(df['age'] > 25) & (df['department'] == '技术部')]", sample_df)
        assert len(result) == 2

    # --- 聚合操作 ---
    def test_groupby_mean(self, sample_df):
        result = self._eval("df.groupby('department')['salary'].mean()", sample_df)
        assert "技术部" in result.index

    def test_value_counts(self, sample_df):
        result = self._eval("df['department'].value_counts()", sample_df)
        assert result["技术部"] == 2

    # --- 排序 ---
    def test_sort_values(self, sample_df):
        result = self._eval("df.sort_values('salary', ascending=False)", sample_df)
        assert result.iloc[0]["name"] == "王五"

    # --- 安全性：拒绝 __dunder__ 访问 ---
    def test_block_dunder_class(self, sample_df):
        with pytest.raises(ValueError, match="私有属性"):
            self._eval("df.__class__", sample_df)

    def test_block_dunder_mro(self, sample_df):
        with pytest.raises(ValueError, match="私有属性"):
            self._eval("df.__class__.__mro__", sample_df)

    def test_block_dunder_subclasses(self, sample_df):
        with pytest.raises(ValueError):
            self._eval("df.__class__.__mro__[1].__subclasses__()", sample_df)

    def test_block_dunder_globals(self, sample_df):
        with pytest.raises(ValueError, match="私有"):
            self._eval("df.__globals__", sample_df)

    # --- 安全性：拒绝危险变量 ---
    def test_block_os_import(self, sample_df):
        with pytest.raises(ValueError, match="不允许的变量名"):
            self._eval("os", sample_df)

    def test_block_sys_import(self, sample_df):
        with pytest.raises(ValueError, match="不允许的变量名"):
            self._eval("sys", sample_df)

    def test_block_builtins(self, sample_df):
        with pytest.raises(ValueError, match="不允许的变量名"):
            self._eval("__builtins__", sample_df)

    # --- 安全性：拒绝危险函数调用 ---
    def test_block_exec(self, sample_df):
        with pytest.raises(ValueError):
            self._eval("exec('import os')", sample_df)

    # --- 字面量 ---
    def test_int_literal(self, sample_df):
        result = self._eval("42", sample_df)
        assert result == 42

    def test_string_literal(self, sample_df):
        result = self._eval("'hello'", sample_df)
        assert result == "hello"

    def test_list_literal(self, sample_df):
        result = self._eval("['a', 'b', 'c']", sample_df)
        assert result == ["a", "b", "c"]

    # --- 算术运算 ---
    def test_arithmetic(self, sample_df):
        result = self._eval("df['salary'] * 12", sample_df)
        assert result.iloc[0] == 180000

    # --- str 访问器 ---
    def test_str_contains(self, sample_df):
        result = self._eval("df[df['name'].str.contains('三')]", sample_df)
        assert len(result) == 1


class TestExpressionKeywordBlacklist:
    """测试表达式关键字黑名单（第一层防护）"""

    def test_block_import(self):
        assert _validate_expression("import os") is not None

    def test_block_exec(self):
        assert _validate_expression("exec('code')") is not None

    def test_block_eval(self):
        assert _validate_expression("eval('code')") is not None

    def test_block_dunder(self):
        assert _validate_expression("df.__class__") is not None

    def test_block_subprocess(self):
        assert _validate_expression("subprocess.call()") is not None

    def test_allow_normal_pandas(self):
        assert _validate_expression("df[df['age'] > 30]") is None

    def test_allow_groupby(self):
        assert _validate_expression("df.groupby('city')['salary'].mean()") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
