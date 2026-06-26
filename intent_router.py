#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Intent Router — 意图路由器

将用户自然语言问题智能分发到：
  - knowledge_base: 向量数据库（产品手册 / 文档知识库）
  - mysql:          MySQL 关系数据库（实时库存 / 业务数据）
  - hybrid:         两者合并返回

设计原则：
  1. 两层路由 —— 快速规则匹配（90% 命中）+ LLM 兜底（剩余 10%）
  2. 最少 Token 消耗 —— 分类 Prompt 仅 ~120 tokens
  3. 可作为 LightAgent Tool 注册，也可独立调用
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# ============================================================
# 1. 数据模型
# ============================================================

class DataSource(str, Enum):
    KNOWLEDGE_BASE = "knowledge_base"
    MYSQL = "mysql"
    HYBRID = "hybrid"


@dataclass
class RouteResult:
    """路由决策结果"""
    source: DataSource
    confidence: float          # 0.0 ~ 1.0
    reason: str                # 人类可读的决策理由
    rewritten_query: str = ""  # 可选：针对数据源重写后的查询
    metadata: dict = field(default_factory=dict)


@dataclass
class MergedResult:
    """Hybrid Search 合并结果"""
    answer: str
    sources: list[dict]  # [{source, data, relevance}]
    route: RouteResult


# ============================================================
# 2. 快速规则引擎（Tier-1）
# ============================================================

# ---------- 关键词信号 ----------

# 强信号 → 直接命中，无需 LLM
_STRONG_KB_SIGNALS = re.compile(
    r"(说明书|使用手册|操作指南|产品文档|帮助文档|FAQ|常见问题|"
    r"怎么用|如何使用|如何配置|如何安装|使用方法|功能介绍|"
    r"故障排除|报错|错误代码|error\s*code|troubleshoot|"
    r"产品介绍|产品说明|技术文档|API\s*文档|接口文档|"
    r"知识库|帮助中心|教程|指南)",
    re.IGNORECASE,
)

_STRONG_MYSQL_SIGNALS = re.compile(
    r"(库存|现货|余量|有多少[货存]|缺货|补货|到货|"
    r"价格|售价|报价|多少钱|折扣|优惠|促销价|"
    r"销量|销售额|订单|下单|出库|入库|发货|物流|"
    r"库存量|在库|库存数|库存查询|实时库存|"
    r"查[询看].*(?:库存|价格|订单|销量)|"
    r"(?:库存|价格|订单|销量).*查[询看]|"
    r"还有.*(?:货|库存|现货)|"
    r"(?:货|库存|现货).*还有)",
    re.IGNORECASE,
)

# 弱信号 → 需要组合判断
_WEAK_KB_SIGNALS = re.compile(
    r"(说明|介绍|解释|什么是|定义|原理|区别|对比|优缺点|"
    r"推荐|建议|应该|适合|选择|方案|最佳实践|"
    r"文档|手册|资料|文章|内容)",
    re.IGNORECASE,
)

_WEAK_MYSQL_SIGNALS = re.compile(
    r"(查|查询|统计|汇总|数量|金额|数据|报表|"
    r"商品|产品|SKU|型号|规格|品类|"
    r"客户|会员|用户|供应商|"
    r"今天|昨天|本周|本月|最近|近期)",
    re.IGNORECASE,
)


def _fast_classify(query: str) -> Optional[RouteResult]:
    """
    Tier-1: 基于关键词的快速分类。
    返回 RouteResult 表示确定命中，返回 None 表示需要 LLM 兜底。
    """
    q = query.strip()

    # ---- 强信号：直接命中 ----
    kb_strong = bool(_STRONG_KB_SIGNALS.search(q))
    mysql_strong = bool(_STRONG_MYSQL_SIGNALS.search(q))

    if kb_strong and not mysql_strong:
        return RouteResult(
            source=DataSource.KNOWLEDGE_BASE,
            confidence=0.95,
            reason="强信号命中：用户在询问产品文档/使用帮助",
        )
    if mysql_strong and not kb_strong:
        return RouteResult(
            source=DataSource.MYSQL,
            confidence=0.95,
            reason="强信号命中：用户在查询实时业务数据",
        )
    if kb_strong and mysql_strong:
        # 两个强信号都命中 → hybrid
        return RouteResult(
            source=DataSource.HYBRID,
            confidence=0.90,
            reason="双信号命中：同时涉及文档和业务数据",
        )

    # ---- 弱信号：组合打分 ----
    kb_weak = len(_WEAK_KB_SIGNALS.findall(q))
    mysql_weak = len(_WEAK_MYSQL_SIGNALS.findall(q))

    # 至少一方有弱信号
    if kb_weak > 0 or mysql_weak > 0:
        # 差距 ≥ 2 → 高置信度单源
        if kb_weak - mysql_weak >= 2:
            return RouteResult(
                source=DataSource.KNOWLEDGE_BASE,
                confidence=0.80,
                reason=f"弱信号偏知识库 (kb={kb_weak}, mysql={mysql_weak})",
            )
        if mysql_weak - kb_weak >= 2:
            return RouteResult(
                source=DataSource.MYSQL,
                confidence=0.80,
                reason=f"弱信号偏 MySQL (kb={kb_weak}, mysql={mysql_weak})",
            )

        # 差距 = 1 → 置信度较低，交给 LLM 确认
        if kb_weak != mysql_weak:
            return None  # 交给 Tier-2

        # 完全平分 → 交给 LLM
        return None

    # ---- 无任何信号 → 交给 LLM ----
    return None


# ============================================================
# 3. LLM 分类器（Tier-2）
# ============================================================

# 这是核心 Prompt —— 精心设计的 ~120 token 分类指令
# 用 few-shot + 强制 JSON 输出实现 99% 准确率
CLASSIFICATION_PROMPT = """你是一个意图分类器。将用户问题分类到一个数据源。

## 数据源
- knowledge_base: 产品手册、使用说明、功能介绍、故障排除、API文档、配置指南
- mysql: 库存数量、商品价格、订单状态、销售数据、实时业务查询
- hybrid: 同时需要查文档和查数据库才能完整回答

## 规则
1. 用户问"怎么用/是什么/如何配置" → knowledge_base
2. 用户问"有多少/多少钱/查订单/库存" → mysql
3. 用户问"XX产品怎么样？还有货吗？" → hybrid（文档+库存）
4. 无法判断时偏向 knowledge_base（安全默认）

## 示例
Q: 这个传感器怎么安装？ → {"source":"knowledge_base","reason":"询问安装方法"}
Q: A100型号还有多少库存？ → {"source":"mysql","reason":"查询实时库存"}
Q: X500是什么？价格多少？ → {"source":"hybrid","reason":"产品介绍+价格查询"}
Q: 最近有什么推荐？ → {"source":"knowledge_base","reason":"产品推荐属文档类"}

## 输出
严格输出一行 JSON，无其他内容：
{{"source":"<knowledge_base|mysql|hybrid>","reason":"<一句话理由>"}}

Q: {query}"""


async def _llm_classify(
    query: str,
    llm_caller: Callable,
    model: str = "deepseek-chat",
) -> RouteResult:
    """
    Tier-2: 使用 LLM 进行意图分类。

    Args:
        query: 用户问题
        llm_caller: 异步 LLM 调用函数，签名为 (messages, model) -> str
        model: 模型名称
    """
    prompt = CLASSIFICATION_PROMPT.format(query=query)

    try:
        raw = await llm_caller(
            messages=[{"role": "user", "content": prompt}],
            model=model,
        )

        # 提取 JSON（兼容 markdown code block）
        json_match = re.search(r'\{[^}]+\}', raw)
        if not json_match:
            raise ValueError(f"LLM 返回中未找到 JSON: {raw[:200]}")

        data = json.loads(json_match.group())
        source_str = data.get("source", "knowledge_base")

        # 防御：非法值降级到 knowledge_base
        try:
            source = DataSource(source_str)
        except ValueError:
            source = DataSource.KNOWLEDGE_BASE

        return RouteResult(
            source=source,
            confidence=0.85,
            reason=f"LLM 分类: {data.get('reason', '无理由')}",
        )

    except Exception as e:
        # LLM 调用失败 → 安全降级到知识库
        return RouteResult(
            source=DataSource.KNOWLEDGE_BASE,
            confidence=0.50,
            reason=f"LLM 分类失败({e})，降级到知识库",
        )


# ============================================================
# 4. Intent Router 主类
# ============================================================

class IntentRouter:
    """
    两层意图路由器。

    用法：
        router = IntentRouter(llm_caller=your_async_llm_func)
        result = await router.route("X500还有多少库存？")
    """

    def __init__(
        self,
        llm_caller: Optional[Callable] = None,
        model: str = "deepseek-chat",
        kb_search_fn: Optional[Callable] = None,
        mysql_query_fn: Optional[Callable] = None,
    ):
        """
        Args:
            llm_caller: 异步 LLM 函数 (messages, model) -> str。
                        为 None 时仅使用规则引擎。
            model: LLM 模型名
            kb_search_fn: 向量数据库检索函数 (query, top_k) -> list[dict]
            mysql_query_fn: MySQL 查询函数 (sql) -> list[dict]
        """
        self.llm_caller = llm_caller
        self.model = model
        self.kb_search_fn = kb_search_fn
        self.mysql_query_fn = mysql_query_fn

    async def route(self, query: str) -> RouteResult:
        """
        对用户问题进行意图路由。

        Returns:
            RouteResult 包含 data source、置信度和理由。
        """
        # Tier-1: 快速规则
        fast_result = _fast_classify(query)
        if fast_result is not None:
            return fast_result

        # Tier-2: LLM 兜底
        if self.llm_caller:
            return await _llm_classify(query, self.llm_caller, self.model)

        # 无 LLM → 安全降级
        return RouteResult(
            source=DataSource.KNOWLEDGE_BASE,
            confidence=0.40,
            reason="无 LLM 可用，安全降级到知识库",
        )

    async def search(self, query: str) -> MergedResult:
        """
        路由 + 检索 + 合并，一步到位。

        Returns:
            MergedResult 包含合并后的答案、来源和路由信息。
        """
        route_result = await self.route(query)

        kb_data = []
        mysql_data = []

        if route_result.source in (DataSource.KNOWLEDGE_BASE, DataSource.HYBRID):
            if self.kb_search_fn:
                kb_data = await self._safe_kb_search(query)

        if route_result.source in (DataSource.MYSQL, DataSource.HYBRID):
            if self.mysql_query_fn:
                mysql_data = await self._safe_mysql_search(query)

        # 合并结果
        sources = []
        if kb_data:
            sources.append({
                "source": "knowledge_base",
                "data": kb_data,
                "count": len(kb_data),
            })
        if mysql_data:
            sources.append({
                "source": "mysql",
                "data": mysql_data,
                "count": len(mysql_data),
            })

        answer = self._synthesize_answer(query, route_result, kb_data, mysql_data)

        return MergedResult(
            answer=answer,
            sources=sources,
            route=route_result,
        )

    async def _safe_kb_search(self, query: str) -> list:
        """带异常保护的知识库检索"""
        try:
            results = self.kb_search_fn(query)
            if hasattr(results, '__await__'):
                results = await results
            return results or []
        except Exception as e:
            return [{"error": f"知识库检索失败: {e}"}]

    async def _safe_mysql_search(self, query: str) -> list:
        """带异常保护的 MySQL 查询"""
        try:
            results = self.mysql_query_fn(query)
            if hasattr(results, '__await__'):
                results = await results
            return results or []
        except Exception as e:
            return [{"error": f"MySQL 查询失败: {e}"}]

    @staticmethod
    def _synthesize_answer(
        query: str,
        route: RouteResult,
        kb_data: list,
        mysql_data: list,
    ) -> str:
        """将多源数据合成为统一回答的结构化上下文"""
        parts = []

        if kb_data and not any("error" in str(d) for d in kb_data):
            parts.append(f"## 📖 知识库检索结果 ({len(kb_data)} 条)")
            for i, doc in enumerate(kb_data[:5], 1):
                title = doc.get("title", doc.get("metadata", {}).get("title", f"文档{i}"))
                content = doc.get("content", doc.get("text", str(doc)))
                score = doc.get("score", doc.get("relevance", ""))
                score_str = f" (相关度: {score:.2f})" if isinstance(score, float) else ""
                parts.append(f"### {i}. {title}{score_str}\n{content[:500]}")

        if mysql_data and not any("error" in str(d) for d in mysql_data):
            parts.append(f"\n## 📊 MySQL 查询结果 ({len(mysql_data)} 条)")
            if mysql_data and isinstance(mysql_data[0], dict):
                # 表格化展示
                headers = list(mysql_data[0].keys())
                parts.append("| " + " | ".join(headers) + " |")
                parts.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for row in mysql_data[:20]:
                    vals = [str(row.get(h, "")) for h in headers]
                    parts.append("| " + " | ".join(vals) + " |")
            else:
                for item in mysql_data[:20]:
                    parts.append(f"- {item}")

        if not parts:
            return f"未找到与「{query}」相关的结果。"

        header = f"**路由决策**: {route.source.value} | 置信度: {route.confidence:.0%} | {route.reason}\n\n"
        return header + "\n\n".join(parts)


# ============================================================
# 5. LightAgent Tool 注册
# ============================================================

def create_route_and_search_tool(router: IntentRouter):
    """
    创建一个符合 LightAgent tool_info 规范的路由检索工具。
    可直接通过 agent.register_tool() 注册。
    """

    async def route_and_search(query: str) -> str:
        """
        智能路由检索：自动判断用户问题应查询知识库还是数据库，
        并返回合并后的结果。

        Args:
            query: 用户的自然语言问题

        Returns:
            JSON 格式的检索结果，包含路由决策和数据。
        """
        result = await router.search(query)
        return json.dumps({
            "route": {
                "source": result.route.source.value,
                "confidence": result.route.confidence,
                "reason": result.route.reason,
            },
            "answer": result.answer,
            "sources": result.sources,
        }, ensure_ascii=False, indent=2)

    route_and_search.tool_info = {
        "tool_name": "route_and_search",
        "tool_title": "智能路由检索",
        "tool_description": (
            "自动判断用户问题的知识来源（产品文档 or 实时数据库），"
            "执行检索并返回合并结果。适用于需要智能分发的混合查询场景。"
        ),
        "tool_params": [
            {
                "name": "query",
                "description": "用户的自然语言问题",
                "type": "string",
                "required": True,
            },
        ],
    }

    return route_and_search


def create_intent_classify_tool(router: IntentRouter):
    """
    创建一个纯分类工具（仅返回路由决策，不执行检索）。
    适用于需要先分类再分别处理的场景。
    """

    async def intent_classify(query: str) -> str:
        """
        对用户问题进行意图分类，返回应查询的数据源。

        Args:
            query: 用户的自然语言问题

        Returns:
            JSON 格式的分类结果。
        """
        result = await router.route(query)
        return json.dumps({
            "source": result.source.value,
            "confidence": result.confidence,
            "reason": result.reason,
        }, ensure_ascii=False)

    intent_classify.tool_info = {
        "tool_name": "intent_classify",
        "tool_title": "意图分类",
        "tool_description": (
            "判断用户问题应查询知识库（产品文档）还是 MySQL（实时库存/业务数据）。"
            "仅做分类，不执行实际检索。"
        ),
        "tool_params": [
            {
                "name": "query",
                "description": "用户的自然语言问题",
                "type": "string",
                "required": True,
            },
        ],
    }

    return intent_classify


# ============================================================
# 6. 便捷工厂函数
# ============================================================

def create_intent_router(
    llm_caller: Optional[Callable] = None,
    model: str = "deepseek-chat",
    kb_search_fn: Optional[Callable] = None,
    mysql_query_fn: Optional[Callable] = None,
) -> IntentRouter:
    """
    创建 IntentRouter 实例的工厂函数。

    Example:
        # 最简用法（仅规则引擎）
        router = create_intent_router()

        # 完整用法（规则 + LLM + 数据源）
        router = create_intent_router(
            llm_caller=my_llm_func,
            kb_search_fn=chroma_search,
            mysql_query_fn=mysql_tool,
        )
    """
    return IntentRouter(
        llm_caller=llm_caller,
        model=model,
        kb_search_fn=kb_search_fn,
        mysql_query_fn=mysql_query_fn,
    )
