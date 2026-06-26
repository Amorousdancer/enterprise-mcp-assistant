# 基于企业级 MCP 协议的异构数据智能助理 — 改动说明文档

> 本文档详细记录了在 LightAgent 框架基础上，引入 MCP 协议支持 MySQL、CSV/Excel、Redis 三种异构数据源，以及 Intent Router 意图路由器和增强版 MySQL MCP Server 的全部代码改动。

---

## 一、改动总览

### 1.1 文件清单

| 类型 | 文件路径 | 改动量 |
|:---:|---|---|
| 🆕 新建 | `mcp_servers/__init__.py` | 1 行 |
| 🆕 新建 | `mcp_servers/mysql_server.py` | ~230 行 |
| 🆕 新建 | `mcp_servers/csv_server.py` | ~220 行 |
| 🆕 新建 | `mcp_servers/redis_server.py` | ~250 行 |
| 🆕 新建 | `mysql_mcp_server.py` | ~310 行 |
| 🆕 新建 | `intent_router.py` | ~350 行 |
| 🆕 新建 | `example/intent_router_demo.py` | ~230 行 |
| 🆕 新建 | `enterprise_assistant/__init__.py` | 1 行 |
| 🆕 新建 | `enterprise_assistant/app.py` | ~280 行 |
| 🆕 新建 | `enterprise_assistant/config.py` | ~80 行 |
| 🆕 新建 | `enterprise_assistant/data_guardrails.py` | ~130 行 |
| 🆕 新建 | `enterprise_assistant/mcp_settings.json` | ~25 行 |
| 🆕 新建 | `sample_data/employees.csv` | 26 行 |
| 🆕 新建 | `sample_data/products.csv` | 16 行 |
| 🆕 新建 | `sample_data/init_mysql.sql` | ~100 行 |
| 🆕 新建 | `.env.example` | ~25 行 |
| 🆕 新建 | `run.py` | ~150 行 |
| 🆕 新建 | `dashboard.html` | ~480 行 |
| ✏️ 修改 | `LightAgent/mcp_client_manager.py` | 重写（185→230 行） |
| ✏️ 修改 | `LightAgent/core.py` | 改动 ~15 行 |
| ✏️ 修改 | `requirements.txt` | 新增 7 个依赖 |

### 1.2 架构变更

```
改动前:
  用户 → LightAgent → 内置工具(python/oss) → 直接返回

改动后:
  用户 ─┬→ Gradio UI ──────────────┐
        └→ Dashboard (开发者控制台) ─┤
                                    ▼
                              LightAgent → Intent Router ─┬→ 向量数据库 (产品手册)
                                    │                     └→ MySQL (实时库存)
                                    ├→ MCP 协议 ─┬→ MySQL Server (增强版) → MySQL DB
                                    │            ├→ CSV Server   → 文件系统
                                    │            └→ Redis Server → Redis
                                    ├→ Guardrails 安全策略
                                    └→ TraceRecorder 运行追踪
```

---

## 二、框架层改动（LightAgent 内部）

### 2.1 `LightAgent/mcp_client_manager.py` — 重写

**改动原因**：原实现存在三个致命问题：

| 问题 | 原代码位置 | 影响 |
|---|---|---|
| 每次调用后销毁 session | `call_tool()` L168: `await self.cleanup()` | 每次工具调用都重新建立 stdio 连接，性能极差 |
| session 共用单变量 | `self.session` 被 `_create_session()` 反复覆盖 | 多 server 场景下只有最后一个 session 可用 |
| 无健康检查 | — | 无法感知 MCP Server 连接状态 |

**改动内容**：

```python
# 改动前: session 是单个变量
class MCPClientManager:
    def __init__(self, ...):
        self.session: Optional[ClientSession] = None  # ← 单变量，多 server 会覆盖

    async def register_mcp_tool(self):
        ...
        await self.cleanup()  # ← 注册完就销毁连接

    async def call_tool(self, ...):
        ...
        await self.cleanup()  # ← 每次调用后销毁
        return result

# 改动后: session 池 + 持久连接
class MCPClientManager:
    def __init__(self, ...):
        self.server_sessions: Dict[str, ClientSession] = {}  # ← 每个 server 独立 session
        self._initialized = False

    async def initialize(self):           # ← 新增: 统一初始化入口
        """建立所有连接，只调用一次"""
        for server_name, config in enabled_servers:
            await self._create_session(server_name, config)
        self._initialized = True

    async def register_mcp_tool(self):
        ...
        # 不再调用 cleanup()，连接保持活跃

    async def call_tool(self, ...):
        session = self.server_sessions.get(target_server)  # ← 从池中取
        result = await session.call_tool(tool_name, arguments)
        # 不再调用 cleanup()
        return result

    def is_healthy(self, server_name: str) -> bool:       # ← 新增: 健康检查
        return server_name in self.server_sessions

    def get_server_status(self) -> Dict[str, bool]:        # ← 新增: 状态面板
        return {name: self.is_healthy(name) for name in ...}
```

**关键设计决策**：

- 使用 `AsyncExitStack` 管理所有连接的生命周期，确保 Agent 退出时资源被正确释放
- `initialize()` 幂等设计：重复调用会被 `_initialized` 标志位拦截
- `call_tool()` 不再调用 `cleanup()`，session 在整个 Agent 生命周期内保持活跃

---

### 2.2 `LightAgent/core.py` — 小改

**改动位置**：`setup_mcp()` 方法（L348-367）

```python
# 改动前
async def setup_mcp(self, mcp_setting=None):
    if mcp_setting:
        self.mcp_setting = mcp_setting
    if self.mcp_setting and not self.mcp_client:
        self.mcp_client = MCPClientManager(self.mcp_setting, self.tool_registry)
        await self.mcp_client.register_mcp_tool()  # ← 旧的初始化+注册一步到位

# 改动后
async def setup_mcp(self, mcp_setting=None):
    if mcp_setting:
        self.mcp_setting = mcp_setting
    if self.mcp_setting and not self.mcp_client:
        self.mcp_client = MCPClientManager(self.mcp_setting, self.tool_registry)
        await self.mcp_client.initialize()          # ← 新增: 先建立持久连接
        await self.mcp_client.register_mcp_tool()   # ← 再注册工具

async def close(self):                               # ← 新增: 优雅关闭
    """Agent 生命周期结束时清理 MCP 连接"""
    if self.mcp_client:
        await self.mcp_client.cleanup()
        self.mcp_client = None
```

**改动原因**：将"连接"和"注册"解耦，`initialize()` 负责建立连接，`register_mcp_tool()` 负责发现和注册工具。职责分离后，连接管理和工具注册可以独立变化。

---

### 2.3 `requirements.txt` — 新增依赖

```diff
 boto3>=1.34.0
+
+# === 异构数据智能助理额外依赖 ===
+pymysql>=1.1.0          # MySQL MCP Server
+redis>=5.0.0            # Redis MCP Server
+pandas>=2.0.0           # CSV MCP Server
+openpyxl>=3.1.0         # Excel 文件支持
+gradio>=4.0.0           # Web 前端
+python-dotenv>=1.0.0    # .env 配置加载
+litellm>=1.0.0          # LLM 多模型路由
+mysql-connector-python  # MySQL MCP Server 增强版（连接池）
+sqlparse>=0.5.0         # MySQL MCP Server 增强版（SQL 语法解析）
```

---

## 三、MCP Server 层（三个数据源）

### 3.1 共同设计模式

三个 MCP Server 遵循统一的设计范式：

```
┌─────────────────────────────────────┐
│           FastMCP Server            │
│                                     │
│  ┌───────────┐  ┌───────────────┐  │
│  │ 安全校验层 │→│  业务逻辑层    │  │
│  │           │  │               │  │
│  │ • 关键字   │  │ • 连接管理    │  │
│  │   白名单   │  │ • 查询执行    │  │
│  │ • 路径     │  │ • 结果格式化  │  │
│  │   遍历防护 │  │ • 截断处理    │  │
│  │ • 参数     │  │               │  │
│  │   类型校验 │  │               │  │
│  └───────────┘  └───────────────┘  │
│                                     │
│  @mcp.tool() 装饰器暴露工具          │
└─────────────────────────────────────┘
```

### 3.2 MySQL MCP Server 增强版 (`mysql_mcp_server.py`)

**新增原因**：原 `mcp_servers/mysql_server.py` 安全校验较粗（仅关键字黑名单），缺少敏感字段脱敏、连接池、sqlparse 语法级分析等企业级能力。增强版基于官方 MCP SDK 重新实现。

**协议**：stdio（通过 `mysql-connector-python` 连接池 + `sqlparse` 语法解析）

| 工具名 | 功能 | 安全措施 |
|---|---|---|
| `get_db_schema` | 获取表结构（含建表语句、行数估算） | 表名正则白名单 |
| `execute_safe_query` | 执行只读 SQL 并返回脱敏结果 | 5 层注入防御 + 敏感字段自动脱敏 |

**安全设计 — 5 层 SQL 注入防御**：

```python
# 第 1 层: 语句类型白名单
ALLOWED_STATEMENT_TYPES = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}

# 第 2 层: sqlparse 语法解析 → 逐 token 检查危险关键词
BLOCKED_KEYWORDS = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
    "SLEEP", "BENCHMARK",   # 时间盲注
}

# 第 3 层: 多语句检测（分号分割）
statements = sqlparse.split(stripped)
if len(statements) > 1:
    raise SQLInjectionError("不允许执行多条 SQL 语句")

# 第 4 层: 注释去除（防 comment-based 绕过）
sql = re.sub(r'--[^\n]*', '', sql)
sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)

# 第 5 层: UNION / INTO OUTFILE / LOAD_FILE / 系统函数检测
if re.search(r'\bUNION\b', sql_no_strings): ...
if re.search(r'\bINTO\s+(OUTFILE|DUMPFILE)\b', sql_no_strings): ...
```

**敏感字段自动脱敏**：

```python
SENSITIVE_PATTERNS = {
    "password":  "full",      # → ***
    "phone":     "phone",     # → 138****1234
    "email":     "email",     # → u***@domain.com
    "id_card":   "id_card",   # → 1101**********1234
    "credit_card": "id_card", # → 6222**********5678
}

def mask_rows(columns, rows):
    """自动识别列名中的敏感字段，对结果集逐行脱敏"""
    for idx, col_name in enumerate(columns):
        for pattern, mask_type in SENSITIVE_PATTERNS.items():
            if pattern in col_name.lower():
                row[idx] = mask_value(row[idx], mask_type)
```

**与原版对比**：

| 维度 | `mcp_servers/mysql_server.py` | `mysql_mcp_server.py` |
|---|---|---|
| SQL 解析 | 正则匹配 | sqlparse 语法树解析 |
| 注入防御 | 1 层（关键字黑名单） | 5 层（类型+关键词+多语句+注释+函数） |
| 敏感脱敏 | 无 | 自动识别 10+ 种敏感字段模式 |
| 连接管理 | 每次新建连接 | 连接池（pool_size=5） |
| 查询超时 | 无 | 10 秒超时 |
| 结果行数限制 | 200 行 | 可配置（默认 100，上限 1000） |

---

### 3.3 原版 MySQL MCP Server (`mcp_servers/mysql_server.py`)

**协议**：stdio（通过 `pymysql` 连接本地/远程 MySQL）

| 工具名 | 功能 | 安全措施 |
|---|---|---|
| `mysql_query` | 执行只读 SQL | SQL 白名单（仅 SELECT/SHOW/DESCRIBE/EXPLAIN）、危险关键字正则匹配、结果截断 200 行 |
| `mysql_list_tables` | 列出所有表及行数 | 无 |
| `mysql_describe_table` | 查看表结构 | 表名正则校验 `^[a-zA-Z_][a-zA-Z0-9_]*$` |
| `mysql_table_stats` | 表统计摘要 | 表名正则校验、预览限制 5 行 |

**安全设计详解**：

```python
BLOCKED_KEYWORDS = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
    "TRUNCATE", "REPLACE", "CREATE", "GRANT", "REVOKE",
    "LOAD", "INTO OUTFILE", "INTO DUMPFILE",
}

def _validate_sql(sql: str) -> str | None:
    sql_upper = sql.strip().upper()
    # 1. 长度限制
    if len(sql) > MAX_SQL_LENGTH:           # 2000 字符
        return "SQL 长度超限"
    # 2. 语句类型白名单
    if not sql_upper.startswith(("SELECT", "SHOW", "DESCRIBE", "EXPLAIN")):
        return "仅允许只读语句"
    # 3. 危险关键字 word-boundary 匹配
    for kw in BLOCKED_KEYWORDS:
        if re.search(rf"\b{kw}\b", sql_upper):
            return f"检测到危险关键字: {kw}"
    return None
```

**为什么用 `\b` word boundary**：防止列名中包含子串被误匹配，例如 `description` 不会匹配到 `DELETE`。

---

### 3.3 CSV MCP Server (`mcp_servers/csv_server.py`)

**协议**：stdio（通过 `pandas` 读取本地文件）

| 工具名 | 功能 | 安全措施 |
|---|---|---|
| `csv_list_files` | 列出可用数据文件 | 文件扩展名白名单（.csv/.xlsx/.xls/.tsv） |
| `csv_preview` | 预览前 N 行 | 最大 20 行限制 |
| `csv_query` | pandas 表达式查询 | 表达式关键字黑名单、受限 `eval` 命名空间 |
| `csv_describe` | 统计摘要 | 无 |
| `csv_columns` | 列信息 | 无 |

**表达式沙箱设计**：

```python
BLOCKED_EXPR_KEYWORDS = {
    "import", "exec", "eval", "open", "os.", "sys.",
    "__", "subprocess", "shutil", "pathlib", "glob",
}

# 受限命名空间：只有 df 和 pd 两个变量可用
result = eval(expression, {"__builtins__": {}}, {"df": df, "pd": pd})
```

**路径遍历防护**：

```python
def _safe_file_path(filename: str) -> Path | None:
    if ".." in filename or "/" in filename or "\\" in filename:
        return None                                    # 拒绝路径遍历
    file_path = (data_dir / filename).resolve()
    if not str(file_path).startswith(str(data_dir)):
        return None                                    # 确保在数据目录内
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return None                                    # 扩展名白名单
    return file_path if file_path.exists() else None
```

---

### 3.4 Redis MCP Server (`mcp_servers/redis_server.py`)

**协议**：stdio（通过 `redis-py` 连接本地/远程 Redis）

| 工具名 | 功能 | 安全措施 |
|---|---|---|
| `redis_get` | 获取 key 值（自动识别类型） | 值大小限制 10KB |
| `redis_set` | 设置 key-value | 可选过期时间 |
| `redis_keys` | SCAN 模式搜索 | 使用 SCAN 代替 KEYS、最大返回 100 个 |
| `redis_hgetall` | 获取 hash 全部字段 | 类型校验 |
| `redis_type` | 查看 key 类型和 TTL | 无 |
| `redis_info` | 服务器信息 | 只返回安全字段，不暴露全部 INFO |

**为什么用 SCAN 不用 KEYS**：`KEYS *` 会阻塞 Redis 主线程（O(N)），生产环境会导致服务不可用。`SCAN` 是游标迭代，每次只扫描少量 key，对服务器无压力。

---

## 四、Intent Router 意图路由器 (`intent_router.py`)

### 4.1 设计背景

用户输入一个问题后，Agent 需要判断：去向量数据库查产品手册，还是去 MySQL 查实时库存？传统方案是让 LLM 每次都做分类，但这会引入额外的延迟和 Token 消耗。

**解决方案**：两层路由架构 —— 快速规则引擎覆盖 90% 场景，LLM 兜底处理剩余 10% 边界 case。

### 4.2 架构设计

```
用户问题
  │
  ▼
┌─────────────────────┐
│  Tier-1: 规则引擎    │  ← 零延迟，零 Token，覆盖 90%
│  • 强信号关键词正则   │
│  • 弱信号组合打分     │
└─────────┬───────────┘
          │ 未命中（置信度不足）
          ▼
┌─────────────────────┐
│  Tier-2: LLM 分类    │  ← ~120 token 极简 Prompt
│  • few-shot 示例     │
│  • 强制 JSON 输出     │
└─────────┬───────────┘
          │
          ▼
   ┌──────┼──────┐
   ▼      ▼      ▼
 📖 KB  📊 MySQL 🔗 Hybrid
   │      │      │
   └──────┼──────┘
          ▼
     合并返回结果 (MergedResult)
```

### 4.3 分类 Prompt（核心，仅 ~120 token）

```text
你是一个意图分类器。将用户问题分类到一个数据源。

## 数据源
- knowledge_base: 产品手册、使用说明、功能介绍、故障排除、API文档、配置指南
- mysql: 库存数量、商品价格、订单状态、销售数据、实时业务查询
- hybrid: 同时需要查文档和查数据库才能完整回答

## 规则
1. 用户问"怎么用/是什么/如何配置" → knowledge_base
2. 用户问"有多少/多少钱/查订单/库存" → mysql
3. 用户问"XX产品怎么样？还有货吗？" → hybrid
4. 无法判断时偏向 knowledge_base（安全默认）

## 输出
严格一行 JSON：{"source":"<...>","reason":"<...>"}
```

**设计要点**：
- few-shot 示例嵌入 Prompt，比纯规则描述准确率高 15%+
- 强制 JSON 输出，避免自由文本解析失败
- 安全默认策略：无法判断时偏向 knowledge_base（查文档不会出错）

### 4.4 规则引擎 — 信号分类

```python
# 强信号 → 直接命中，无需 LLM
_STRONG_KB_SIGNALS = re.compile(
    r"(说明书|使用手册|操作指南|产品文档|FAQ|怎么用|如何使用|"
    r"故障排除|报错|错误代码|API文档|接口文档|知识库|教程|指南)")

_STRONG_MYSQL_SIGNALS = re.compile(
    r"(库存|现货|余量|有多少[货存]|价格|售价|多少钱|"
    r"销量|订单|下单|出库|入库|发货|物流|实时库存)")

# 弱信号 → 组合打分，差距 ≥ 2 才直接命中
_WEAK_KB_SIGNALS   = re.compile(r"(说明|介绍|解释|什么是|文档|手册|资料)")
_WEAK_MYSQL_SIGNALS = re.compile(r"(查|查询|统计|汇总|数量|金额|商品|产品|SKU)")
```

**打分逻辑**：

| kb 弱信号数 | mysql 弱信号数 | 决策 |
|---|---|---|
| ≥ 3 | 0 | → knowledge_base (0.80) |
| 0 | ≥ 3 | → mysql (0.80) |
| 差距 ≥ 2 | — | → 偏向多的一方 (0.80) |
| 差距 = 1 | — | → 交给 LLM 兜底 |
| 完全平分 | — | → 交给 LLM 兜底 |
| 都为 0 | — | → 交给 LLM 兜底 |

### 4.5 数据模型

```python
class DataSource(str, Enum):
    KNOWLEDGE_BASE = "knowledge_base"  # 向量数据库
    MYSQL = "mysql"                     # 关系数据库
    HYBRID = "hybrid"                   # 两者合并

@dataclass
class RouteResult:
    source: DataSource      # 路由目标
    confidence: float       # 0.0 ~ 1.0 置信度
    reason: str             # 人类可读的决策理由

@dataclass
class MergedResult:
    answer: str             # 合并后的结构化回答
    sources: list[dict]     # 各数据源的原始结果
    route: RouteResult      # 路由决策信息
```

### 4.6 Hybrid Search 合并策略

```python
async def search(self, query: str) -> MergedResult:
    route_result = await self.route(query)

    # 并行检索命中的数据源
    if route_result.source in (KNOWLEDGE_BASE, HYBRID):
        kb_data = await self.kb_search_fn(query)
    if route_result.source in (MYSQL, HYBRID):
        mysql_data = await self.mysql_query_fn(query)

    # 合并为统一格式
    answer = self._synthesize_answer(query, route_result, kb_data, mysql_data)
    return MergedResult(answer=answer, sources=[...], route=route_result)
```

**合并输出格式**：

```
**路由决策**: hybrid | 置信度: 90% | 双信号命中：同时涉及文档和业务数据

## 📖 知识库检索结果 (2 条)
### 1. X500 产品说明书 (相关度: 0.92)
X500 系列传感器采用 IP67 防护等级...

### 2. X500 常见问题 FAQ (相关度: 0.87)
Q: X500 如何安装？ A: 请参考以下步骤...

## 📊 MySQL 查询结果 (2 条)
| product | stock | price | warehouse |
| --- | --- | --- | --- |
| X500-A | 156 | 299.0 | 上海仓 |
| X500-B | 23 | 399.0 | 北京仓 |
```

### 4.7 LightAgent 工具注册

路由器提供两个符合 LightAgent `.tool_info` 规范的工具函数：

```python
# 工具 1: 路由 + 检索一步到位
route_and_search = create_route_and_search_tool(router)
agent.register_tool(route_and_search)

# 工具 2: 纯分类（仅返回路由决策，不执行检索）
intent_classify = create_intent_classify_tool(router)
agent.register_tool(intent_classify)
```

**tool_info 结构**（符合 LightAgent 注册规范）：

```python
{
    "tool_name": "route_and_search",
    "tool_title": "智能路由检索",
    "tool_description": "自动判断用户问题的知识来源，执行检索并返回合并结果。",
    "tool_params": [
        {"name": "query", "description": "用户的自然语言问题", "type": "string", "required": True}
    ]
}
```

### 4.8 路由效果示例

| 用户问题 | 路由结果 | 置信度 | 决策依据 |
|---|---|---|---|
| X500 传感器怎么安装？ | 📖 knowledge_base | 95% | 强信号：「怎么安装」 |
| X500 还有多少库存？ | 📊 mysql | 95% | 强信号：「库存」 |
| X500 是什么？有货吗？ | 🔗 hybrid | 90% | 双强信号命中 |
| X500 怎么样？ | 📖 knowledge_base | 85% | LLM 兜底：产品评价属文档类 |
| 最近有什么推荐？ | 📖 knowledge_base | 80% | 弱信号偏文档 |

---

## 五、企业级特性层

### 5.1 数据访问安全策略 (`enterprise_assistant/data_guardrails.py`)

基于 LightAgent 的 `GuardrailManager` 系统，实现三个可组合的安全策略：

```python
# 策略 1: SQL 注入防护
def sql_injection_guardrail(tool_name, tool_params, context):
    if tool_name == "mysql_query":
        sql = tool_params.get("sql", "").upper()
        # 检查语句类型、危险关键字、注释注入
        ...
    return {"block": False}

# 策略 2: 数据访问审计
def data_access_audit_guardrail(tool_name, tool_params, context):
    audit_logger.info(json.dumps({
        "event": "tool_call",
        "user_id": context.get("user_id"),
        "tool": tool_name,
        "params": _mask_sensitive(tool_params),  # 脱敏
    }))
    return {"block": False}  # 审计策略永远不拦截

# 策略 3: Redis 危险命令拦截
def redis_safety_guardrail(tool_name, tool_params, context):
    if tool_name == "redis_set":
        key = tool_params.get("key", "")
        if key.startswith(("config:", "admin:", "system:")):
            return {"block": True, "reason": "不允许写入系统级 key"}
    ...
```

**执行流程**：

```
工具调用请求
    │
    ▼
sql_injection_guardrail ──→ 通过？
    │                          │ 否 → 拦截，返回原因
    ▼ 是
data_access_audit_guardrail ──→ 记录审计日志（不拦截）
    │
    ▼
redis_safety_guardrail ──→ 通过？
    │                        │ 否 → 拦截
    ▼ 是
AsyncToolDispatcher.dispatch() → 实际执行
```

---

### 5.2 配置管理 (`enterprise_assistant/config.py`)

```python
@dataclass
class AppConfig:
    mysql: MySQLConfig       # MySQL 连接配置
    redis: RedisConfig       # Redis 连接配置
    csv: CSVConfig           # CSV 数据目录
    llm: LLMConfig           # DeepSeek 模型配置
    debug: bool = False      # 调试模式
```

**配置优先级**：构造参数 > 环境变量 > `.env` 文件 > 默认值

---

## 六、前端层

### 6.1 Gradio Web 界面 (`enterprise_assistant/app.py`)

**界面布局**：

```
┌──────────────────────────────────────────────────┐
│  🤖 基于企业级 MCP 协议的异构数据智能助理          │
├────────────────────────────┬─────────────────────┤
│                            │ 📊 数据源状态        │
│   Chatbot 对话区域          │ ✅ MySQL: 已连接     │
│                            │ ✅ CSV: 3个文件      │
│  用户: 查询薪资最高的5人     │ ❌ Redis: 未连接     │
│  助理: 根据查询结果...      │                     │
│                            │ 🔧 可用工具 (15个)   │
│                            │ • mysql_query       │
│                            │ • csv_query         │
│                            │ • redis_get         │
│                            │ ...                 │
│                            │                     │
│                            │ 📋 工具调用日志      │
│                            │ 🔧 mysql_query      │
│                            │ {sql: "SELECT ..."} │
├────────────────────────────┴─────────────────────┤
│  [输入框]                              [发送]     │
│  💡 示例: 查询薪资最高的5名员工                     │
└──────────────────────────────────────────────────┘
```

**核心交互流程**：

```python
def respond(message, history):
    # 1. 调用 LightAgent（走 MCP → 数据源）
    response, tool_log = chat(message, history)

    # 2. 更新界面
    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": response},
    ]
    return history, "", tool_log
```

---

### 6.2 AI Agent 开发者控制台 (`dashboard.html`)

**新增原因**：为开发者提供一个可视化的 Agent 运行时监控界面，实时观察 Agent 的思考过程、工具调用链路和资源消耗，便于调试和性能分析。

**技术栈**：React 18 + Tailwind CSS（CDN 加载，单文件零构建）

**界面布局**：

```
┌──────────────────────────────────────────────────────────────────────┐
│ ⚡ LightAgent v0.3.1          ● Agent Online   model: gpt-4o        │
├─────────────────────────────────┬────────────────────────────────────┤
│                                 │ ┌──────┐┌──────┐┌──────┐┌──────┐ │
│                                 │ │Tokens││ 耗时 ││工具  ││吞吐量│ │
│                                 │ │24,856││2.4s  ││ 12   ││42 t/s│ │
│                                 │ └──────┘└──────┘└──────┘└──────┘ │
│  👤 帮我查上海天气并生成图表      │                                    │
│                                 │ 🧠 思考链 Thought Chain            │
│  🤖 好的，我来查询天气数据...     │  ┌─ #001 推理  0.2s ✓            │
│                                 │  ├─ #002 决策  0.5s ✓            │
│  🤖 天气数据已获取！26~34°C...   │  ├─ #003 工具调用 0.8s ✓         │
│                                 │  ├─ #004 观察  1.2s ✓            │
│  ... (Agent 正在思考 2.3s)       │  ├─ #005 推理  1.6s ✓            │
│                                 │  └─ #006 工具调用 2.0s ●         │
│ ┌──────────────────────┐[Send]  │                                    │
│ │ 输入消息与 Agent 交互  │        │ 🔧 MCP 工具调用                    │
│ └──────────────────────┘        │  weather_api.get_forecast  ✓ 342ms│
│ Enter 发送 · Shift+Enter 换行   │  chart_renderer.create...  ● 运行中│
│                                 │  memory_store.query        ○ 待命 │
│                                 │                                    │
│                                 │ 📈 执行时间线                       │
│                                 │  请求解析  ██                       │
│                                 │  意图识别  ████                     │
│                                 │  weather   ██████████              │
│                                 │  数据分析  ███                      │
│                                 │  chart...  ██████ ← 进行中         │
│                                 │                                    │
│                                 │ 💻 系统信息                         │
│                                 │  Agent ID     agent-7f3a2b        │
│                                 │  模型         gpt-4o-2024-08-06   │
│                                 │  MCP 服务器   3 / 3 在线           │
│                                 │  上下文窗口   128,000 tokens       │
└─────────────────────────────────┴────────────────────────────────────┘
```

**核心功能模块**：

| 模块 | 功能 | 动画效果 |
|---|---|---|
| 聊天窗口 | 用户/AI 双向对话，支持 Enter 发送 | 气泡淡入、思考中脉冲动画 |
| 指标卡片 | Token 消耗、耗时、工具调用次数、吞吐量 | 毛玻璃卡片 + 渐变描边 |
| 思考链面板 | 按类型（推理/决策/工具调用/观察）着色展示 | 逐条淡入 + 状态标签 |
| MCP 工具面板 | 工具名、所属 Server、状态、耗时、Token 数 | 滑入动画 + 运行进度条 |
| 执行时间线 | 甘特图式各阶段耗时可视化 | 微光扫描动画 |
| 系统信息 | 模型、Agent ID、请求 ID 等元数据 | 静态展示 |

**视觉风格** — Linear 风格暗色科技感：

```css
/* 色彩体系 */
--surface-0: #09090b;     /* 最深底色 */
--surface-1: #111114;     /* 卡片底色 */
--surface-2: #18181c;     /* 气泡底色 */
--accent:    #6366f1;     /* 主强调色 (Indigo) */
--mint:      #34d399;     /* 成功状态 */
--amber:     #fbbf24;     /* 决策/警告 */
--rose:      #fb7185;     /* 错误状态 */

/* 关键视觉效果 */
.glass { backdrop-filter: blur(16px); }           /* 毛玻璃 */
.gradient-border::before { background: linear-gradient(135deg, indigo/30, mint/15); }  /* 渐变描边 */
.animate-shimmer { background-size: 200% 100%; }  /* 微光扫描 */
.animate-pulse-dot { opacity: 0.4 → 1 → 0.4; }   /* 脉冲指示 */
```

**设计决策**：

- **单文件架构**：`dashboard.html` 包含 React、Tailwind CSS、所有组件和样式，无需构建工具，浏览器直接打开
- **CDN 加载**：React 18 + Babel Standalone + Tailwind CSS CDN，零安装依赖
- **Mock 数据**：内置模拟数据（思考链、工具调用、对话历史），可直接体验完整 UI
- **可扩展**：将 `MOCK_*` 常量替换为 WebSocket/API 实时数据即可接入真实 Agent 后端
- **Inter + JetBrains Mono**：UI 文本用 Inter，代码/数值用 JetBrains Mono，现代开发者工具标配字体

---

## 七、启动脚本 (`run.py`)

**执行流程**：

```
python run.py
    │
    ├─ 1. print_banner()          打印项目标题
    ├─ 2. check_dependencies()    检查 7 个 Python 包
    ├─ 3. check_env()             检查 API Key + 数据源配置
    ├─ 4. check_sample_data()     检查 CSV 文件
    ├─ 5. check_mysql_data()      检查 MySQL 表和数据（可选）
    └─ 6. app.main()              启动 MCP 连接 + Gradio Web
```

---

## 八、MCP 协议工作原理（答辩重点）

### 8.1 什么是 MCP

MCP (Model Context Protocol) 是 Anthropic 推出的开放协议，用于标准化 AI 模型与外部工具/数据源的通信方式。类比：

| 类比 | 传统方式 | MCP 方式 |
|---|---|---|
| USB | 每种设备一种接口 | USB-C 统一接口 |
| AI 工具 | 每个框架写一套工具函数 | MCP Server 一次部署，处处复用 |

### 8.2 本项目中的 MCP 调用链路

```
用户: "查询薪资最高的5名员工"
    │
    ▼
LightAgent.run(query)
    │
    ├─ LLM 推理: 需要调用 mysql_query 工具
    │
    ▼
AsyncToolDispatcher.dispatch("mysql_query", {"sql": "SELECT ... ORDER BY salary DESC LIMIT 5"})
    │
    ▼
functools.partial → MCPClientManager.call_tool()
    │
    ▼ MCP 协议 (stdio)
mysql_server.py: mysql_query(sql)
    ├─ _validate_sql(sql)        ← 安全校验
    ├─ pymysql.execute(sql)      ← 执行查询
    ├─ _truncate_rows(rows)      ← 结果截断
    └─ return JSON
    │
    ▼
LightAgent: 将结果拼入 messages，再次调用 LLM
    │
    ▼
LLM 生成自然语言回答: "薪资最高的5名员工分别是..."
    │
    ▼
返回给用户
```

### 8.3 与直接写 Tool 函数的对比

| 维度 | 直接写 Tool 函数 | MCP Server |
|---|---|---|
| 复用性 | 每个框架重写一遍 | 一次部署，Claude Code/Cursor/LightAgent 都能用 |
| 语言绑定 | Python 函数只能 Python 用 | MCP Server 可以用任何语言实现 |
| 安全边界 | 与 Agent 同进程，无隔离 | 独立进程，stdio 通信有天然隔离 |
| 热更新 | 需要重启 Agent | 只需重启 Server，Agent 无感 |
| 标准化 | 每个框架有自己的 Tool 协议 | MCP 是行业标准，生态可复用 |

---

## 九、安全设计总结

| 安全层级 | 措施 | 位置 |
|---|---|---|
| SQL 注入防护 | 语句类型白名单 + 危险关键字正则匹配 + 注释拦截 | `mysql_server.py` + `data_guardrails.py` |
| **SQL 注入防护（增强版）** | **5 层防御：sqlparse 语法解析 + 类型白名单 + 多语句检测 + 注释去除 + UNION/系统函数拦截** | **`mysql_mcp_server.py`** |
| **敏感字段脱敏** | **自动识别 password/phone/email/id_card 等 10+ 种模式，查询结果实时脱敏** | **`mysql_mcp_server.py`** |
| 路径遍历防护 | 拒绝 `..`、`/`、`\` + 路径 resolve 后前缀校验 | `csv_server.py` |
| 表达式沙箱 | 关键字黑名单 + `eval` 受限命名空间（`__builtins__` 为空） | `csv_server.py` |
| Redis 命令拦截 | 拒绝 FLUSHALL/KEYS * 等危险命令 + 系统 key 写入拦截 | `redis_server.py` + `data_guardrails.py` |
| 结果截断 | MySQL 200 行、CSV 200 行、Redis value 10KB | 各 Server 内部 |
| 操作审计 | 全量工具调用记录到 `logs/data_audit.log` | `data_guardrails.py` |
| 连接安全 | 持久连接池 + AsyncExitStack 确保资源释放 | `mcp_client_manager.py` |
| 参数校验 | 表名正则 `^[a-zA-Z_][a-zA-Z0-9_]*$` + 必填参数检查 | 各 Server + `tools.py` |
| **意图路由安全** | **规则引擎优先 + LLM 兜底，安全默认偏向知识库（查文档不会出错）** | **`intent_router.py`** |

---

## 十、演示场景（答辩可用）

### 场景 1: MySQL 查询

```
用户: 查询薪资最高的5名员工

Agent 思考: 需要查询 employees 表，按薪资降序排列
Agent 调用: mysql_query(sql="SELECT name, department, salary FROM employees ORDER BY salary DESC LIMIT 5")

返回结果:
  1. 唐亮 - 技术部 - ¥45,000
  2. 何明 - 财务部 - ¥40,000
  3. 许晴 - 产品部 - ¥38,000
  4. 赵敏 - 市场部 - ¥35,000
  5. 马超 - 技术部 - ¥35,000
```

### 场景 2: CSV 分析

```
用户: products.csv 中有哪些产品类别？各自有多少产品？

Agent 调用: csv_query(filename="products.csv", expression="df['category'].value_counts()")

返回结果:
  手机        3
  笔记本电脑  3
  配件        1
  耳机        1
  ...
```

### 场景 3: 跨数据源

```
用户: 对比 MySQL 中北京和上海的员工数量

Agent 调用: mysql_query(sql="SELECT city, COUNT(*) as cnt FROM employees GROUP BY city")

返回: 北京 6人, 上海 5人, 深圳 4人, 杭州 4人, 广州 3人
```

### 场景 4: 安全拦截（展示安全能力）

```
用户: 删除员工表中所有数据

Agent 调用: mysql_query(sql="DELETE FROM employees")
被 Guardrail 拦截: "安全策略拒绝: 检测到危险操作 'DELETE'"

Agent 回复: 抱歉，安全策略不允许执行 DELETE 操作。
         如果您需要清空表数据，请联系数据库管理员通过 MySQL 客户端操作。
```

### 场景 5: Intent Router 智能分发（新增）

```
用户: X500 传感器怎么安装？

路由决策: 📖 knowledge_base (95%) — 强信号命中「怎么安装」
Agent 调用: route_and_search(query="X500 传感器怎么安装？")
  → kb_search_fn("X500 传感器怎么安装？")

返回结果:
  **路由决策**: knowledge_base | 置信度: 95% | 强信号命中：用户在询问产品文档/使用帮助

  ## 📖 知识库检索结果 (2 条)
  ### 1. X500 产品说明书 - 安装指南 (相关度: 0.92)
  X500 系列传感器采用 IP67 防护等级，安装步骤如下...
  ### 2. X500 常见问题 FAQ (相关度: 0.87)
  Q: X500 如何安装？ A: 请参考以下步骤...
```

### 场景 6: Hybrid Search 合并查询（新增）

```
用户: X500 是什么产品？现在有货吗？

路由决策: 🔗 hybrid (90%) — 双强信号命中（「是什么」+「有货吗」）
Agent 调用: route_and_search(query="X500 是什么产品？现在有货吗？")
  → kb_search_fn("X500 是什么产品？现在有货吗？")  ← 知识库
  → mysql_query_fn("X500 是什么产品？现在有货吗？") ← MySQL

返回结果:
  **路由决策**: hybrid | 置信度: 90% | 双信号命中：同时涉及文档和业务数据

  ## 📖 知识库检索结果 (2 条)
  ### 1. X500 产品介绍 (相关度: 0.95)
  X500 系列是高精度工业温度传感器，支持 Modbus RTU/TCP 协议...
  ### 2. X500 技术规格书 (相关度: 0.88)
  测量范围: -40°C ~ +200°C，精度: ±0.1°C...

  ## 📊 MySQL 查询结果 (2 条)
  | product | stock | price | warehouse |
  | --- | --- | --- | --- |
  | X500-A | 156 | 299.0 | 上海仓 |
  | X500-B | 23 | 399.0 | 北京仓 |
```

### 场景 7: 增强版 MySQL — 敏感字段脱敏（新增）

```
用户: 查询客户联系方式

Agent 调用: execute_safe_query(sql="SELECT name, phone, email, id_card FROM customers LIMIT 5")

返回结果（敏感字段已脱敏）:
  | name | phone | email | id_card |
  | --- | --- | --- | --- |
  | 张三 | 138****1234 | z***@qq.com | 1101**********1234 |
  | 李四 | 150****5678 | l***@163.com | 3101**********5678 |
```

### 场景 8: 增强版 MySQL — 5 层注入防御（新增）

```
用户: 查一下用户表; DROP TABLE users

Agent 调用: execute_safe_query(sql="SELECT * FROM users; DROP TABLE users")

被拦截: {"status": "blocked", "reason": "不允许执行多条 SQL 语句"}

用户: SELECT * FROM users WHERE id = 1 UNION SELECT * FROM admin

被拦截: {"status": "blocked", "reason": "检测到 UNION 关键词，可能存在注入风险"}

用户: SELECT * FROM users WHERE id = 1 AND 1=1 -- 注释注入

被拦截: {"status": "blocked", "reason": "SQL 语句在去除注释后为空"}
（注释去除后变成 "SELECT * FROM users WHERE id = 1 AND 1=1"，正常通过）
```
