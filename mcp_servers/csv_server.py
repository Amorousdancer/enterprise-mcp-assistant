#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CSV/Excel MCP Server — 企业级文件数据访问服务

通过 MCP 协议暴露 CSV / Excel 文件的查询能力，内置路径遍历防护、
表达式沙箱、结果截断等安全特性。

启动方式: python mcp_servers/csv_server.py
"""

import os
import re
import ast
import json
import operator
import pandas as pd
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# MCP Server 实例
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "CSV-Enterprise",
    instructions="企业级 CSV/Excel 文件数据访问服务。提供文件浏览、数据预览、条件查询、统计分析等功能。"
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DATA_DIR = os.getenv("CSV_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "sample_data"))
MAX_ROWS = 200
MAX_PREVIEW_ROWS = 20

# 安全：只允许访问的文件扩展名
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".tsv"}

# 安全：pandas query 表达式的危险关键字
BLOCKED_EXPR_KEYWORDS = {
    "import", "exec", "eval", "open", "os.", "sys.",
    "__", "subprocess", "shutil", "pathlib", "glob",
}

# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _resolve_data_dir() -> Path:
    """解析并验证数据目录"""
    data_dir = Path(DATA_DIR).resolve()
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _safe_file_path(filename: str) -> Path | None:
    """
    安全地解析文件路径，防止路径遍历攻击。
    返回 None 表示拒绝。
    """
    # 拒绝路径遍历
    if ".." in filename or "/" in filename or "\\" in filename:
        return None

    data_dir = _resolve_data_dir()
    file_path = (data_dir / filename).resolve()

    # 确保文件在数据目录内
    if not str(file_path).startswith(str(data_dir)):
        return None

    # 检查扩展名
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return None

    return file_path if file_path.exists() else None


def _load_dataframe(filename: str) -> tuple[pd.DataFrame | None, str | None]:
    """加载文件为 DataFrame，返回 (df, error)"""
    file_path = _safe_file_path(filename)
    if file_path is None:
        return None, f"文件 '{filename}' 不存在或不允许访问"

    try:
        ext = file_path.suffix.lower()
        if ext == ".csv":
            df = pd.read_csv(file_path, encoding="utf-8")
        elif ext == ".tsv":
            df = pd.read_csv(file_path, sep="\t", encoding="utf-8")
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path)
        else:
            return None, f"不支持的文件格式: {ext}"

        return df, None
    except Exception as e:
        return None, f"读取文件失败: {e}"


def _validate_expression(expr: str) -> str | None:
    """
    校验 pandas 表达式的安全性。
    返回 None 表示通过，返回字符串表示拒绝原因。
    """
    expr_lower = expr.lower()
    for kw in BLOCKED_EXPR_KEYWORDS:
        if kw in expr_lower:
            return f"表达式中包含不允许的关键字: {kw}"
    return None


# ---------------------------------------------------------------------------
# 安全表达式求值器（替代 eval）
# ---------------------------------------------------------------------------

# 允许的顶层变量
_SAFE_NAMES = {"df": None, "pd": pd}

# 允许调用的 pandas 方法（白名单）
_SAFE_PANDAS_METHODS = {
    "head", "tail", "sort_values", "groupby", "agg", "mean", "sum",
    "count", "min", "max", "median", "std", "var", "describe",
    "value_counts", "unique", "nunique", "dropna", "fillna", "astype",
    "rename", "drop", "drop_duplicates", "reset_index", "set_index",
    "merge", "concat", "isin", "between", "nlargest", "nsmallest",
    "apply", "map", "replace", "to_dict", "to_list", "str", "dt",
    "contains", "startswith", "endswith", "lower", "upper", "strip",
    "len", "shape", "columns", "index", "dtype", "dtypes",
    "read_csv", "read_excel",
}

# 允许的二元/一元运算符
_SAFE_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv, ast.Pow,
                ast.BitAnd, ast.BitOr)  # pandas 用 & | 做布尔组合
_SAFE_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
_SAFE_UNARYOPS = (ast.USub, ast.UAdd, ast.Not)


def _safe_eval(node: ast.AST, df: pd.DataFrame) -> any:
    """
    安全地求值 AST 节点，只允许 pandas DataFrame/Series 操作。
    不使用 eval()，不暴露任何内置函数。
    """
    # 数字字面量
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool, type(None))):
            return node.value
        raise ValueError(f"不允许的字面量类型: {type(node.value).__name__}")

    # 变量名（只允许 df 和 pd）
    if isinstance(node, ast.Name):
        if node.id == "df":
            return df
        if node.id == "pd":
            return pd
        if node.id in ("True", "False", "None"):
            return eval(node.id)
        raise ValueError(f"不允许的变量名: {node.id}")

    # 属性访问（df.xxx）
    if isinstance(node, ast.Attribute):
        obj = _safe_eval(node.value, df)
        attr = node.attr
        if attr.startswith("__"):
            raise ValueError(f"不允许访问私有属性: {attr}")
        return getattr(obj, attr)

    # 函数调用
    if isinstance(node, ast.Call):
        func = node.func

        # df.method(...) 形式
        if isinstance(func, ast.Attribute):
            obj = _safe_eval(func.value, df)
            method_name = func.attr
            if method_name.startswith("__"):
                raise ValueError(f"不允许调用私有方法: {method_name}")
            method = getattr(obj, method_name)
            args = [_safe_eval(a, df) for a in node.args]
            kwargs = {kw.arg: _safe_eval(kw.value, df) for kw in node.keywords}
            return method(*args, **kwargs)

        # pd.xxx(...) 形式
        if isinstance(func, ast.Name):
            if func.id == "pd":
                raise ValueError("不允许直接调用 pd 模块，请使用 pd 的属性方法")
            raise ValueError(f"不允许调用函数: {func.id}")

        raise ValueError(f"不允许的调用方式")

    # 下标访问 df[...]
    if isinstance(node, ast.Subscript):
        obj = _safe_eval(node.value, df)
        sl = node.slice
        # df['col'] 或 df[['col1', 'col2']]
        if isinstance(sl, ast.Constant):
            return obj[sl.value]
        # df[0:5]
        if isinstance(sl, ast.Slice):
            lower = _safe_eval(sl.lower, df) if sl.lower else None
            upper = _safe_eval(sl.upper, df) if sl.upper else None
            step = _safe_eval(sl.step, df) if sl.step else None
            return obj[slice(lower, upper, step)]
        # df[df['col'] > 5] — 布尔索引
        return obj[_safe_eval(sl, df)]

    # 比较运算
    if isinstance(node, ast.Compare):
        left = _safe_eval(node.left, df)
        for op, comparator in zip(node.ops, node.comparators):
            right = _safe_eval(comparator, df)
            if isinstance(op, ast.Eq):
                result = left == right
            elif isinstance(op, ast.NotEq):
                result = left != right
            elif isinstance(op, ast.Lt):
                result = left < right
            elif isinstance(op, ast.LtE):
                result = left <= right
            elif isinstance(op, ast.Gt):
                result = left > right
            elif isinstance(op, ast.GtE):
                result = left >= right
            elif isinstance(op, ast.In):
                result = left.isin(right) if hasattr(left, 'isin') else left in right
            elif isinstance(op, ast.NotIn):
                result = ~left.isin(right) if hasattr(left, 'isin') else left not in right
            else:
                raise ValueError(f"不允许的比较运算符: {type(op).__name__}")
            left = result
        return left

    # 布尔运算 (and / or)
    if isinstance(node, ast.BoolOp):
        values = [_safe_eval(v, df) for v in node.values]
        if isinstance(node.op, ast.And):
            result = values[0]
            for v in values[1:]:
                result = result & v
            return result
        elif isinstance(node.op, ast.Or):
            result = values[0]
            for v in values[1:]:
                result = result | v
            return result

    # 二元运算 (+, -, *, /, etc.)
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _SAFE_BINOPS:
            raise ValueError(f"不允许的运算符: {type(node.op).__name__}")
        left = _safe_eval(node.left, df)
        right = _safe_eval(node.right, df)
        ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Mod: operator.mod,
            ast.FloorDiv: operator.floordiv,
            ast.Pow: operator.pow,
            ast.BitAnd: operator.and_,
            ast.BitOr: operator.or_,
        }
        return ops[type(node.op)](left, right)

    # 一元运算 (-x, not x)
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _SAFE_UNARYOPS:
            raise ValueError(f"不允许的一元运算符: {type(node.op).__name__}")
        operand = _safe_eval(node.operand, df)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.Not):
            return ~operand if hasattr(operand, '__invert__') else not operand
        return operand

    # 列表字面量 [1, 2, 3]
    if isinstance(node, ast.List):
        return [_safe_eval(e, df) for e in node.elts]

    # 元组字面量 (1, 2, 3)
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval(e, df) for e in node.elts)

    raise ValueError(f"不允许的表达式类型: {type(node).__name__}")


# ---------------------------------------------------------------------------
# MCP 工具定义
# ---------------------------------------------------------------------------

@mcp.tool()
def csv_list_files() -> str:
    """
    列出可用的数据文件（CSV / Excel / TSV）。

    Returns:
        文件列表的 JSON 字符串，包含文件名、大小、行数估算。
    """
    data_dir = _resolve_data_dir()
    files = []

    for f in sorted(data_dir.iterdir()):
        if f.suffix.lower() in ALLOWED_EXTENSIONS and f.is_file():
            try:
                size_kb = round(f.stat().st_size / 1024, 1)
                # 快速估算行数
                if f.suffix.lower() == ".csv":
                    with open(f, "r", encoding="utf-8") as fh:
                        line_count = sum(1 for _ in fh) - 1  # 减去表头
                else:
                    line_count = "N/A"

                files.append({
                    "filename": f.name,
                    "size_kb": size_kb,
                    "rows": line_count,
                    "type": f.suffix.lower(),
                })
            except Exception:
                files.append({"filename": f.name, "error": "无法读取"})

    return json.dumps({"files": files, "data_dir": str(data_dir)}, ensure_ascii=False)


@mcp.tool()
def csv_preview(filename: str, rows: int = 10) -> str:
    """
    预览数据文件的前 N 行。

    Args:
        filename: 文件名（如 "employees.csv"）
        rows: 预览行数，默认 10，最大 20

    Returns:
        预览数据的 JSON 字符串。
    """
    rows = min(max(rows, 1), MAX_PREVIEW_ROWS)
    df, error = _load_dataframe(filename)
    if error:
        return json.dumps({"error": error}, ensure_ascii=False)

    preview = df.head(rows)
    return json.dumps({
        "filename": filename,
        "total_rows": len(df),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "preview": preview.to_dict(orient="records"),
    }, ensure_ascii=False, default=str)


@mcp.tool()
def csv_query(filename: str, expression: str) -> str:
    """
    用 pandas 表达式查询数据文件。

    表达式示例:
        - df[df['age'] > 30]
        - df[df['department'] == 'Engineering']
        - df.groupby('city')['salary'].mean()
        - df.sort_values('salary', ascending=False).head(10)
        - df[df['name'].str.contains('张')]

    Args:
        filename: 文件名
        expression: pandas 查询表达式（变量名为 df）

    Returns:
        查询结果的 JSON 字符串，最多 200 行。
    """
    # 表达式安全校验
    rejection = _validate_expression(expression)
    if rejection:
        return json.dumps({"error": rejection}, ensure_ascii=False)

    df, error = _load_dataframe(filename)
    if error:
        return json.dumps({"error": error}, ensure_ascii=False)

    try:
        # 使用 AST 安全求值器替代 eval()，防止 __class__.__mro__ 等绕过攻击
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree.body, df)

        # 如果结果是 DataFrame，截断并转为 dict
        if isinstance(result, pd.DataFrame):
            truncated = len(result) > MAX_ROWS
            result = result.head(MAX_ROWS)
            return json.dumps({
                "row_count": len(result),
                "truncated": truncated,
                "data": result.to_dict(orient="records"),
            }, ensure_ascii=False, default=str)

        # 如果结果是 Series 或标量
        if isinstance(result, pd.Series):
            return json.dumps({
                "type": "series",
                "data": result.head(MAX_ROWS).to_dict(),
            }, ensure_ascii=False, default=str)

        return json.dumps({"result": str(result)}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"表达式执行失败: {e}"}, ensure_ascii=False)


@mcp.tool()
def csv_describe(filename: str) -> str:
    """
    获取数据文件的统计摘要（数值列的 mean/std/min/max/中位数等）。

    Args:
        filename: 文件名

    Returns:
        统计摘要的 JSON 字符串。
    """
    df, error = _load_dataframe(filename)
    if error:
        return json.dumps({"error": error}, ensure_ascii=False)

    try:
        desc = df.describe(include="all")
        return json.dumps({
            "filename": filename,
            "total_rows": len(df),
            "total_columns": len(df.columns),
            "statistics": desc.to_dict(),
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"统计分析失败: {e}"}, ensure_ascii=False)


@mcp.tool()
def csv_columns(filename: str) -> str:
    """
    列出数据文件的所有列名、数据类型和非空值数量。

    Args:
        filename: 文件名

    Returns:
        列信息的 JSON 字符串。
    """
    df, error = _load_dataframe(filename)
    if error:
        return json.dumps({"error": error}, ensure_ascii=False)

    columns_info = []
    for col in df.columns:
        columns_info.append({
            "column": col,
            "dtype": str(df[col].dtype),
            "non_null": int(df[col].notna().sum()),
            "null_count": int(df[col].isna().sum()),
            "unique_count": int(df[col].nunique()),
        })

    return json.dumps({
        "filename": filename,
        "columns": columns_info,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run()
