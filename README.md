# 基于 LightAgent 的企业级 MCP 异构数据智能助手

> 在 [LightAgent](https://github.com/wanxingai/LightAgent) 超轻量 AI Agent 框架基础上，二次开发实现的企业级 MCP 异构数据智能助理系统。

---

## 项目简介

本项目基于上海万兴 AI & 上海财经大学张立文教授研究组开源的 LightAgent 框架（v0.9.0），在其核心能力之上进行了企业级二次开发，主要新增了 **MCP 协议异构数据源接入**、**意图路由器**、**三层安全护栏**、**Gradio Web 界面** 和 **开发者控制台** 等模块，使 Agent 具备对接企业 MySQL、CSV/Excel、Redis 等多种异构数据源的能力。

### 核心改动

| 模块 | 说明 | 类型 |
|---|---|---|
| `mysql_mcp_server.py` | MySQL MCP Server（5 层注入防御 + 敏感字段脱敏） | 新增 |
| `mcp_servers/csv_server.py` | CSV/Excel MCP Server（表达式沙箱 + 路径遍历防护） | 新增 |
| `mcp_servers/redis_server.py` | Redis MCP Server（SCAN 替代 KEYS + 危险命令拦截） | 新增 |
| `intent_router.py` | 双层意图路由器（规则快筛 + LLM 兜底） | 新增 |
| `enterprise_assistant/` | Gradio Web 应用（聊天界面 + 数据源状态 + 工具日志） | 新增 |
| `enterprise_assistant/data_guardrails.py` | 三层安全护栏（SQL 注入 / Redis 危险操作 / 访问审计） | 新增 |
| `dashboard.html` | AI Agent 开发者控制台（React + Tailwind 单文件） | 新增 |
| `run.py` | 一键启动脚本（依赖检查 + 环境检查 + 启动 Gradio） | 新增 |
| `sample_data/` | 演示用数据（员工表、产品表、MySQL 初始化脚本） | 新增 |
| `LightAgent/mcp_client_manager.py` | MCP 客户端管理器（重写：持久连接池 + 健康检查） | 修改 |
| `LightAgent/core.py` | Agent 核心（小改：MCP 初始化解耦 + 优雅关闭） | 修改 |

---

## 系统架构

```
用户输入
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    Gradio Web 界面                           │
│         聊天窗口 + 数据源状态面板 + 工具调用日志               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  LightAgent 核心引擎                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  安全护栏层   │  │  意图路由器   │  │  ToT 思维链推理   │  │
│  │  (Guardrails)│  │ (Intent      │  │  (Tree of Thought)│  │
│  │              │  │  Router)     │  │                   │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────────────┘  │
│         │                 │                                  │
│         ▼                 ▼                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              MCP 协议层 (Model Context Protocol)       │   │
│  │  ┌─────────┐    ┌─────────┐    ┌─────────┐          │   │
│  │  │  MySQL  │    │   CSV   │    │  Redis  │          │   │
│  │  │ Server  │    │ Server  │    │ Server  │          │   │
│  │  └────┬────┘    └────┬────┘    └────┬────┘          │   │
│  └───────┼──────────────┼──────────────┼───────────────┘   │
└──────────┼──────────────┼──────────────┼────────────────────┘
           │              │              │
           ▼              ▼              ▼
       MySQL DB       文件系统         Redis
    (enterprise_db)  (CSV/Excel)    (键值存储)
```

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 框架 | LightAgent v0.9.0（Python 3.10+） |
| LLM | OpenAI 兼容接口 / LiteLLM（支持 DeepSeek、GPT-4 等） |
| MCP 协议 | `mcp` SDK + FastMCP（stdio + SSE 双协议） |
| 数据源 | MySQL 8.0 / CSV/Excel / Redis |
| 安全 | sqlparse 语法解析 / 正则白名单 / eval 沙箱 |
| 前端 | Gradio（聊天界面）/ React 18 + Tailwind CSS（控制台） |
| 可观测 | TraceRecorder + Langfuse 集成 |

---

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone https://github.com/Amorousdancer/enterprise-mcp-assistant.git
cd enterprise-mcp-assistant

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入以下配置：
```

```env
# LLM 配置
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat

# MySQL 配置
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=enterprise_db

# Redis 配置（可选）
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

### 3. 初始化演示数据

```bash
# 创建演示数据库和表
mysql -u root -p < sample_data/init_mysql.sql
```

### 4. 启动

```bash
python run.py
```

启动后访问 `http://127.0.0.1:7860` 即可使用。

---

## 功能演示

### MySQL 自然语言查询

```
用户: 查询薪资最高的5名员工

Agent 调用: mysql_query(sql="SELECT name, department, salary FROM employees ORDER BY salary DESC LIMIT 5")

返回:
  1. 唐亮 - 技术部 - ¥45,000
  2. 何明 - 财务部 - ¥40,000
  3. 许晴 - 产品部 - ¥38,000
  4. 赵敏 - 市场部 - ¥35,000
  5. 马超 - 技术部 - ¥35,000
```

### CSV 数据分析

```
用户: products.csv 中有哪些产品类别？

Agent 调用: csv_query(filename="products.csv", expression="df['category'].value_counts()")

返回:
  手机        3
  笔记本电脑  3
  配件        1
  ...
```

### 安全拦截演示

```
用户: 删除员工表中所有数据

被 Guardrail 拦截: "安全策略拒绝: 检测到危险操作 'DELETE'"
```

### 敏感字段自动脱敏（增强版 MySQL Server）

```
用户: 查询客户联系方式

返回（已脱敏）:
  | 姓名 | 手机号     | 邮箱          | 身份证号           |
  | 张三 | 138****1234 | z***@qq.com   | 1101**********1234 |
```

### 意图路由智能分发

```
用户: X500 传感器怎么安装？
路由决策: 📖 knowledge_base (95%) — 强信号命中「怎么安装」

用户: X500 还有多少库存？
路由决策: 📊 mysql (95%) — 强信号命中「库存」

用户: X500 是什么产品？有货吗？
路由决策: 🔗 hybrid (90%) — 双信号命中
```

---

## 安全设计

| 安全层级 | 措施 |
|---|---|
| SQL 注入防护 | 5 层防御：sqlparse 语法解析 + 语句类型白名单 + 多语句检测 + 注释去除 + UNION/系统函数拦截 |
| 敏感字段脱敏 | 自动识别 password/phone/email/id_card 等 10+ 种模式，查询结果实时脱敏 |
| 路径遍历防护 | 拒绝 `..`、`/`、`\` + resolve 后前缀校验 |
| 表达式沙箱 | 关键字黑名单 + `eval` 受限命名空间（`__builtins__` 为空） |
| Redis 命令拦截 | 拒绝 FLUSHALL/KEYS * 等危险命令 + 系统 key 写入拦截 |
| 操作审计 | 全量工具调用记录到 `logs/data_audit.log` |

---

## 项目结构

```
├── LightAgent/                    # LightAgent 核心框架（原始 + 修改）
│   ├── core.py                    # Agent 引擎（修改：MCP 初始化解耦）
│   ├── mcp_client_manager.py      # MCP 客户端（重写：持久连接池）
│   ├── tools.py                   # 工具注册与调度
│   ├── guardrails.py              # 护栏系统
│   ├── flow.py                    # 工作流引擎
│   └── ...
├── mcp_servers/                   # MCP 数据源服务器（新增）
│   ├── csv_server.py              # CSV/Excel
│   └── redis_server.py            # Redis
├── mysql_mcp_server.py            # MySQL MCP Server（新增）
├── intent_router.py               # 意图路由器（新增）
├── enterprise_assistant/          # 企业级 Web 应用（新增）
│   ├── app.py                     # Gradio 主界面
│   ├── config.py                  # 配置管理
│   ├── data_guardrails.py         # 数据安全护栏
│   └── mcp_settings.json          # MCP 服务器配置
├── sample_data/                   # 演示数据（新增）
│   ├── employees.csv
│   ├── products.csv
│   └── init_mysql.sql
├── dashboard.html                 # 开发者控制台（新增）
├── run.py                         # 一键启动脚本（新增）
└── requirements.txt               # Python 依赖
```

---

## 致谢

- [LightAgent](https://github.com/wanxingai/LightAgent) — 上海万兴 AI & 上海财经大学张立文教授研究组开发的超轻量 AI Agent 框架
- [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) — Anthropic 推出的开放协议

---

## License

本项目基于 [Apache 2.0](LICENSE) 协议开源。二次开发部分遵循相同协议。
