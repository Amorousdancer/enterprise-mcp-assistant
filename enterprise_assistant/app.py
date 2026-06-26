#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于企业级 MCP 协议的异构数据智能助理 — Gradio 前端

功能:
1. 自然语言对话界面，支持多轮对话
2. 实时显示数据源连接状态
3. 展示 Agent 的工具调用过程
4. 支持流式输出

启动方式: python enterprise_assistant/app.py
"""

import os
import sys
import json
import asyncio
import logging
from pathlib import Path

# 禁用 Gradio 分析和更新检查
os.environ["GRADIO_ANALYTICS_ENABLED"] = "false"
from datetime import datetime

import gradio as gr

# 确保项目根目录在 sys.path 中
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from LightAgent import LightAgent
from enterprise_assistant.config import load_config
from enterprise_assistant.data_guardrails import get_all_guardrails


# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT_DIR / "logs" / "assistant.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("enterprise_assistant")

# 确保 logs 目录存在
(ROOT_DIR / "logs").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
agent_instance: LightAgent | None = None
mcp_initialized = False
tool_call_log: list[str] = []


# ---------------------------------------------------------------------------
# Agent 初始化
# ---------------------------------------------------------------------------

def init_agent() -> LightAgent:
    """初始化 LightAgent 实例"""
    global agent_instance

    if agent_instance is not None:
        return agent_instance

    config = load_config()

    # 检查 API Key
    if not config.llm.api_key:
        raise ValueError(
            "未找到 DEEPSEEK_API_KEY 或 OPENAI_API_KEY 环境变量。\n"
            "请在 .env 文件中设置: DEEPSEEK_API_KEY=your_key"
        )

    logger.info(f"正在初始化 Agent... 模型: {config.llm.model}")

    agent_instance = LightAgent(
        name="异构数据智能助理",
        instructions="""你是一个企业级数据智能助理，能够通过 MCP 协议访问多种异构数据源：
- MySQL 关系数据库：查询员工、产品、订单等业务数据
- CSV/Excel 文件：分析本地数据文件
- Redis 键值存储：查询缓存和会话数据

你的职责：
1. 理解用户的自然语言问题
2. 选择合适的数据源和工具
3. 生成安全的查询操作
4. 用清晰的中文回答用户，并附上关键数据

注意事项：
- 查询结果用表格或列表展示，方便阅读
- 涉及金额时标注单位（元）
- 如果查询出错，分析原因并给出建议
- 不要执行任何写入操作（INSERT/UPDATE/DELETE），除非用户明确要求""",
        role="企业数据分析师",
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        tool_guardrails=get_all_guardrails(),
        debug=config.debug,
    )

    logger.info("Agent 初始化完成")
    return agent_instance


async def setup_mcp_async():
    """异步初始化 MCP 连接"""
    global mcp_initialized

    if mcp_initialized:
        return

    agent = init_agent()

    # 加载 MCP 配置
    mcp_settings_path = ROOT_DIR / "enterprise_assistant" / "mcp_settings.json"
    if not mcp_settings_path.exists():
        logger.warning(f"MCP 配置文件不存在: {mcp_settings_path}")
        return

    with open(mcp_settings_path, "r", encoding="utf-8") as f:
        mcp_settings = json.load(f)

    logger.info("正在连接 MCP Servers...")
    await agent.setup_mcp(mcp_setting=mcp_settings)
    mcp_initialized = True
    logger.info("MCP Servers 连接完成")


_event_loop = None

def setup_mcp():
    """同步包装：在 Gradio 启动前调用"""
    global _event_loop
    import threading

    _event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_event_loop)

    # 在后台线程运行事件循环，保持 MCP 子进程存活
    def run_loop():
        asyncio.set_event_loop(_event_loop)
        _event_loop.run_forever()

    _loop_thread = threading.Thread(target=run_loop, daemon=True)
    _loop_thread.start()

    # 在该事件循环中执行 MCP 初始化
    future = asyncio.run_coroutine_threadsafe(setup_mcp_async(), _event_loop)
    future.result()  # 等待完成

    # 通知 LightAgent 核心使用同一事件循环
    from LightAgent.core import set_shared_event_loop
    set_shared_event_loop(_event_loop)


# ---------------------------------------------------------------------------
# 数据源状态检测
# ---------------------------------------------------------------------------

def get_data_source_status() -> str:
    """获取各数据源的连接状态"""
    statuses = []

    # MySQL
    try:
        import pymysql
        config = load_config()
        conn = pymysql.connect(
            host=config.mysql.host,
            port=config.mysql.port,
            user=config.mysql.user,
            password=config.mysql.password,
            database=config.mysql.database,
            connect_timeout=3,
        )
        conn.close()
        statuses.append("✅ **MySQL**: 已连接")
    except Exception as e:
        statuses.append(f"❌ **MySQL**: 连接失败 ({e})")

    # CSV
    csv_dir = ROOT_DIR / "sample_data"
    csv_files = list(csv_dir.glob("*.csv")) + list(csv_dir.glob("*.xlsx"))
    if csv_files:
        file_list = ", ".join(f.name for f in csv_files)
        statuses.append(f"✅ **CSV/Excel**: {len(csv_files)} 个文件可用 ({file_list})")
    else:
        statuses.append("⚠️ **CSV/Excel**: 未找到数据文件")

    # Redis
    try:
        import redis
        config = load_config()
        r = redis.Redis(
            host=config.redis.host,
            port=config.redis.port,
            db=config.redis.db,
            password=config.redis.password or None,
            socket_timeout=3,
        )
        r.ping()
        statuses.append("✅ **Redis**: 已连接")
    except Exception as e:
        statuses.append(f"❌ **Redis**: 连接失败 ({e})")

    return "\n\n".join(statuses)


def get_available_tools() -> str:
    """获取已注册的 MCP 工具列表"""
    agent = init_agent()
    tools = agent.tool_registry.openai_function_schemas
    if not tools:
        return "暂无已注册工具"

    tool_list = []
    for t in tools:
        name = t["function"]["name"]
        desc = t["function"].get("description", "")[:60]
        tool_list.append(f"- `{name}`: {desc}")

    return "\n".join(tool_list)


# ---------------------------------------------------------------------------
# 聊天处理
# ---------------------------------------------------------------------------

def chat(message: str, history: list[dict]) -> tuple:
    """
    处理用户消息，返回回复。

    Args:
        message: 用户输入
        history: 对话历史 (Gradio messages 格式: [{"role": "user", "content": ...}, ...])

    Returns:
        (更新后的history, 工具调用日志)
    """
    agent = init_agent()

    # history 已经是 messages 格式，直接传给 LightAgent
    agent_history = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history
        if msg.get("role") in ("user", "assistant") and msg.get("content")
    ]

    try:
        logger.info(f"用户提问: {message}")

        response = agent.run(
            query=message,
            stream=False,
            history=agent_history,
            user_id="gradio_user",
            max_retry=5,
        )

        if response is None:
            response = "（助理已处理完毕，但未生成文本回复）"

        logger.info(f"Agent 回复: {response[:100]}...")

        # 更新工具调用日志
        tool_log = format_tool_calls()

        return response, tool_log

    except Exception as e:
        error_msg = f"⚠️ 处理出错: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg, ""


def _msg_get(msg, key, default=None):
    """兼容 dict 和 Pydantic 对象的属性访问"""
    if isinstance(msg, dict):
        return msg.get(key, default)
    return getattr(msg, key, default)


def format_tool_calls() -> str:
    """格式化最近的工具调用记录"""
    agent = init_agent()
    history = agent.get_history()

    tool_calls = []
    for msg in history:
        role = _msg_get(msg, "role")
        tc_list = _msg_get(msg, "tool_calls")

        if role == "assistant" and tc_list:
            for tc in tc_list:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "{}")
                else:
                    func = getattr(tc, "function", None)
                    name = getattr(func, "name", "unknown") if func else "unknown"
                    args = getattr(func, "arguments", "{}") if func else "{}"
                try:
                    args_dict = json.loads(args) if isinstance(args, str) else args
                    args_str = json.dumps(args_dict, ensure_ascii=False, indent=2)
                except Exception:
                    args_str = str(args)
                tool_calls.append(f"🔧 调用工具: `{name}`\n```json\n{args_str}\n```")

        elif role == "tool":
            content = _msg_get(msg, "content", "")
            # 截断过长的工具返回
            if len(content) > 500:
                content = content[:500] + "\n... [已截断]"
            tool_calls.append(f"📋 工具返回:\n```\n{content}\n```")

    return "\n\n---\n\n".join(tool_calls[-6:]) if tool_calls else "暂无工具调用记录"


# ---------------------------------------------------------------------------
# Gradio 界面构建
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    """构建 Gradio 界面"""

    with gr.Blocks(
        title="🤖 异构数据智能助理",
    ) as demo:

        # ---- 标题 ----
        gr.Markdown("""
        # 🤖 基于企业级 MCP 协议的异构数据智能助理

        > 通过 MCP 协议统一访问 MySQL、CSV/Excel、Redis 三种异构数据源，
        > 用自然语言提问即可获取数据洞察。
        """)

        with gr.Row():
            # ---- 左侧：对话区域 ----
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="对话",
                    height=450,
                )
                with gr.Row():
                    msg_input = gr.Textbox(
                        placeholder="试试问我：查询薪资最高的5名员工 / products.csv 有哪些类别？ / Redis 有哪些 key？",
                        show_label=False,
                        scale=5,
                        container=False,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)

                with gr.Row():
                    clear_btn = gr.Button("🗑️ 清空对话", size="sm")
                    example_btns = gr.Examples(
                        examples=[
                            "查询员工表中薪资最高的5个人",
                            "统计各部门的平均薪资",
                            "products.csv 中有哪些产品类别？",
                            "查看 Redis 服务器信息",
                            "对比北京和上海的员工数量",
                        ],
                        inputs=msg_input,
                        label="💡 示例问题",
                    )

            # ---- 右侧：状态面板 ----
            with gr.Column(scale=1):
                gr.Markdown("### 📊 数据源状态")
                status_output = gr.Markdown(
                    value="⏳ 正在检测...",
                    elem_classes="status-panel",
                )

                gr.Markdown("### 🔧 可用工具")
                tools_output = gr.Markdown(
                    value="⏳ 正在加载...",
                    elem_classes="tool-log",
                )

                gr.Markdown("### 📋 工具调用日志")
                tool_log_output = gr.Markdown(
                    value="暂无调用记录",
                    elem_classes="tool-log",
                )

        # ---- 事件绑定 ----
        def respond(message, history):
            if not message.strip():
                return history, "", tool_log_output.value

            # 调用 Agent（传入当前 history，不包含本次消息）
            response, tool_log = chat(message, history)

            # 添加用户消息和助手回复到 history
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": response},
            ]

            return history, "", tool_log

        msg_input.submit(
            respond,
            inputs=[msg_input, chatbot],
            outputs=[chatbot, msg_input, tool_log_output],
        )
        send_btn.click(
            respond,
            inputs=[msg_input, chatbot],
            outputs=[chatbot, msg_input, tool_log_output],
        )

        def clear_chat():
            agent = init_agent()
            # 清空 Agent 的对话历史
            agent.chat_params["messages"] = [
                m for m in agent.chat_params["messages"]
                if m.get("role") == "system"
            ]
            return [], "暂无调用记录"

        clear_btn.click(clear_chat, outputs=[chatbot, tool_log_output])

        # 启动时检测状态
        demo.load(get_data_source_status, outputs=status_output)
        demo.load(get_available_tools, outputs=tools_output)

    return demo


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    """主函数"""
    print("=" * 60)
    print("  🤖 异构数据智能助理 v1.0")
    print("  基于企业级 MCP 协议")
    print("=" * 60)

    # 1. 初始化 MCP 连接
    print("\n📡 正在连接 MCP Servers...")
    setup_mcp()

    # 2. 获取工具数量
    agent = init_agent()
    tool_count = len(agent.tool_registry.openai_function_schemas)
    print(f"✅ 已注册 {tool_count} 个 MCP 工具\n")

    # 3. 启动 Gradio
    print("🌐 正在启动 Web 界面...")
    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        quiet=True,
    )


if __name__ == "__main__":
    main()
