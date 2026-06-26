#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Intent Router 集成示例

演示如何将 IntentRouter 集成到 LightAgent 中，
实现「知识库 vs MySQL」的智能分发和 Hybrid Search。

运行前确保：
  1. .env 中配置了 DEEPSEEK_API_KEY
  2. MySQL MCP Server 已启动（或 mock）
  3. 向量数据库已就绪（或 mock）
"""

import asyncio
import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from LightAgent import LightAgent
from intent_router import (
    IntentRouter,
    create_intent_router,
    create_route_and_search_tool,
    create_intent_classify_tool,
    DataSource,
)


# ============================================================
# Mock 数据源（替换为你的实际实现）
# ============================================================

async def mock_llm_caller(messages: list, model: str = "deepseek-chat") -> str:
    """
    Mock LLM —— 实际使用时替换为：
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key="...", base_url="...")
        resp = await client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content
    """
    # 简单的关键词匹配模拟 LLM 分类
    q = messages[-1]["content"].lower()
    if any(w in q for w in ["库存", "价格", "订单", "多少"]):
        return '{"source":"mysql","reason":"查询实时业务数据"}'
    if any(w in q for w in ["怎么", "如何", "是什么", "文档"]):
        return '{"source":"knowledge_base","reason":"查询产品文档"}'
    return '{"source":"knowledge_base","reason":"安全默认"}'


def mock_kb_search(query: str, top_k: int = 5) -> list[dict]:
    """
    Mock 向量数据库检索 —— 实际使用时替换为：
        import chromadb
        collection = chromadb_client.get_collection("product_docs")
        results = collection.query(query_texts=[query], n_results=top_k)
        return [{"content": doc, "score": score} for doc, score in ...]
    """
    return [
        {
            "title": "X500 产品说明书 - 安装指南",
            "content": f"关于「{query}」的产品文档内容示例。X500 系列传感器采用 IP67 防护等级...",
            "score": 0.92,
            "metadata": {"source": "product_manual_v3.2.pdf", "page": 42},
        },
        {
            "title": "X500 常见问题 FAQ",
            "content": f"Q: {query}? A: 请参考以下步骤...",
            "score": 0.87,
            "metadata": {"source": "faq_2024.md", "section": "troubleshooting"},
        },
    ]


def mock_mysql_query(query: str) -> list[dict]:
    """
    Mock MySQL 查询 —— 实际使用时替换为 MCP 工具调用：
        result = await mcp_client.call_tool("mysql_query", {"sql": generated_sql})
        return json.loads(result)
    """
    return [
        {"product": "X500-A", "stock": 156, "price": 299.00, "warehouse": "上海仓"},
        {"product": "X500-B", "stock": 23, "price": 399.00, "warehouse": "北京仓"},
    ]


# ============================================================
# 示例 1: 纯路由测试（不依赖 LightAgent）
# ============================================================

async def demo_routing():
    """演示路由分类效果"""
    print("=" * 60)
    print("  示例 1: Intent Router 分类测试")
    print("=" * 60)

    router = create_intent_router(
        llm_caller=mock_llm_caller,
        kb_search_fn=mock_kb_search,
        mysql_query_fn=mock_mysql_query,
    )

    test_queries = [
        # 知识库类
        "X500传感器怎么安装？",
        "这个产品的技术参数是什么？",
        "如何配置 Modbus 通信？",
        "报错 E-003 是什么意思？",
        # MySQL 类
        "X500还有多少库存？",
        "查一下A100型号的价格",
        "最近一周的订单情况",
        "上海仓还有多少货？",
        # Hybrid 类
        "X500是什么产品？现在有货吗？",
        "推荐一款传感器，顺便看看库存",
        # 模糊类（需要 LLM 兜底）
        "X500怎么样？",
        "有什么好的方案？",
    ]

    for q in test_queries:
        result = await router.route(q)
        icon = {"knowledge_base": "📖", "mysql": "📊", "hybrid": "🔗"}
        print(f"\n  Q: {q}")
        print(f"  → {icon[result.source.value]} {result.source.value}"
              f"  (置信度: {result.confidence:.0%})  {result.reason}")


# ============================================================
# 示例 2: Hybrid Search 完整流程
# ============================================================

async def demo_hybrid_search():
    """演示完整的路由 + 检索 + 合并"""
    print("\n" + "=" * 60)
    print("  示例 2: Hybrid Search 完整流程")
    print("=" * 60)

    router = create_intent_router(
        llm_caller=mock_llm_caller,
        kb_search_fn=mock_kb_search,
        mysql_query_fn=mock_mysql_query,
    )

    queries = [
        "X500怎么安装？",              # 纯知识库
        "X500还有多少库存？",           # 纯 MySQL
        "X500是什么产品？现在有货吗？",  # Hybrid
    ]

    for q in queries:
        print(f"\n{'─' * 50}")
        print(f"  Q: {q}")
        print(f"{'─' * 50}")
        result = await router.search(q)
        print(result.answer)


# ============================================================
# 示例 3: 注册为 LightAgent Tool
# ============================================================

async def demo_lightagent_integration():
    """演示如何将路由器注册到 LightAgent"""
    print("\n" + "=" * 60)
    print("  示例 3: LightAgent 集成")
    print("=" * 60)

    # 创建路由器
    router = create_intent_router(
        llm_caller=mock_llm_caller,
        kb_search_fn=mock_kb_search,
        mysql_query_fn=mock_mysql_query,
    )

    # 创建符合 LightAgent 规范的工具
    route_tool = create_route_and_search_tool(router)
    classify_tool = create_intent_classify_tool(router)

    # 注册到 LightAgent
    agent = LightAgent(
        name="智能客服",
        instructions="""你是一个智能客服助手，能够：
1. 查阅产品文档回答使用问题
2. 查询数据库获取实时库存和价格
3. 综合多个数据源给出完整回答

当用户提问时，优先使用 route_and_search 工具进行智能检索。""",
        model="deepseek-chat",
        # api_key 和 base_url 从环境变量读取
    )

    # 注册工具
    agent.register_tool(route_tool)
    agent.register_tool(classify_tool)

    print(f"\n  ✅ 已注册 {len(agent.tool_registry.openai_function_schemas)} 个工具:")
    for schema in agent.tool_registry.openai_function_schemas:
        print(f"     - {schema['function']['name']}: {schema['function']['description'][:50]}...")

    # 测试运行（非流式）
    print("\n  🔄 测试 Agent 调用...")
    try:
        response = agent.run(
            query="X500传感器还有库存吗？",
            stream=False,
            max_retry=3,
        )
        print(f"\n  Agent 回复:\n  {response[:300]}...")
    except Exception as e:
        print(f"\n  ⚠️ Agent 调用失败（可能缺少 API Key）: {e}")
        print("  这是正常的，因为 mock_llm_caller 不能替代真实的 Agent LLM 调用。")
        print("  在生产环境中，router 使用的是 Agent 同一个 LLM client。")


# ============================================================
# 示例 4: 作为 LightAgent 原生工具（.tool_info 模式）
# ============================================================

def demo_tool_info():
    """展示 tool_info 结构，验证与 LightAgent 注册系统的兼容性"""
    print("\n" + "=" * 60)
    print("  示例 4: tool_info 结构验证")
    print("=" * 60)

    router = create_intent_router()
    tool = create_route_and_search_tool(router)

    print(f"\n  tool_info:\n  {json.dumps(tool.tool_info, ensure_ascii=False, indent=2)}")

    # 验证必要字段
    info = tool.tool_info
    assert "tool_name" in info, "缺少 tool_name"
    assert "tool_description" in info, "缺少 tool_description"
    assert "tool_params" in info, "缺少 tool_params"
    assert all(p.get("name") for p in info["tool_params"]), "参数缺少 name"
    print("\n  ✅ tool_info 结构验证通过，可直接注册到 LightAgent")


# ============================================================
# 主入口
# ============================================================

async def main():
    print("\n🚀 Intent Router 演示\n")

    await demo_routing()
    await demo_hybrid_search()
    demo_tool_info()
    await demo_lightagent_integration()

    print("\n" + "=" * 60)
    print("  演示完成！")
    print("=" * 60)
    print("""
  接下来你可以：
  1. 将 mock 函数替换为你的真实实现
  2. 在 enterprise_assistant/app.py 中导入 router
  3. 将 route_and_search 工具注册到你的 Agent
  """)


if __name__ == "__main__":
    asyncio.run(main())
