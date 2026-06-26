#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
MCP 客户端管理器 — 企业级增强版

改进点:
1. 持久连接池：每个 MCP Server 维护独立 session，不随工具调用销毁
2. 健康检查：支持检测各 Server 连接状态
3. 优雅关闭：Agent 生命周期结束时统一清理资源

作者: [weego/WXAI-Team]
最后更新: 2026-06-26
"""

from functools import partial
from typing import Optional, Dict, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from contextlib import AsyncExitStack

from .tools import ToolRegistry


class MCPClientManager:
    """企业级 MCP 客户端管理器（持久连接版）"""

    def __init__(self, config: dict, tool_registry: ToolRegistry):
        self.config = config
        self.tool_registry = tool_registry
        self.exit_stack = AsyncExitStack()
        # 每个 server 一个独立 session（不再共用 self.session）
        self.server_sessions: Dict[str, ClientSession] = {}
        self._initialized = False

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    async def initialize(self):
        """
        统一初始化所有已启用的 MCP Server 连接。
        只应调用一次；重复调用会被忽略。
        """
        if self._initialized:
            return

        enabled_servers = [
            (name, cfg)
            for name, cfg in self.config.get("mcpServers", {}).items()
            if not cfg.get("disabled", False)
        ]

        for server_name, server_config in enabled_servers:
            try:
                await self._create_session(server_name, server_config)
                print(f"✅ MCP Server [{server_name}] 连接成功")
            except Exception as e:
                print(f"❌ MCP Server [{server_name}] 连接失败: {e}")

        self._initialized = True

    async def _create_session(self, server_name: str, config: dict):
        """为指定 server 创建独立 session 并存入连接池"""
        if 'url' in config:
            # SSE 传输
            streams_context = sse_client(
                url=config['url'],
                headers=config.get('headers', {})
            )
            streams = await self.exit_stack.enter_async_context(streams_context)
            session = await self.exit_stack.enter_async_context(
                ClientSession(*streams)
            )
        else:
            # stdio 传输
            server_params = StdioServerParameters(
                command=config["command"],
                args=config["args"],
                env=config.get("env")
            )
            transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read_stream, write_stream = transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )

        await session.initialize()
        self.server_sessions[server_name] = session

    async def cleanup(self):
        """清理所有连接资源（Agent 退出时调用）"""
        try:
            await self.exit_stack.aclose()
        except (RuntimeError, ExceptionGroup):
            # 忽略 anyio/mcp 在 Windows 上的跨 task 清理错误
            pass
        self.server_sessions.clear()
        self._initialized = False

    def is_healthy(self, server_name: str) -> bool:
        """检查指定 server 的连接是否存活"""
        return server_name in self.server_sessions

    def get_server_status(self) -> Dict[str, bool]:
        """获取所有 server 的连接状态"""
        all_servers = self.config.get("mcpServers", {})
        return {
            name: self.is_healthy(name)
            for name, cfg in all_servers.items()
            if not cfg.get("disabled", False)
        }

    # ------------------------------------------------------------------
    # 工具注册
    # ------------------------------------------------------------------

    async def register_mcp_tool(self) -> bool:
        """自动注册所有已连接 MCP Server 的工具到 ToolRegistry"""
        registered_count = 0

        for server_name, session in self.server_sessions.items():
            try:
                tools_response = await session.list_tools()
                print(f"🔍 注册 MCP Server [{server_name}] 的工具...")

                for tool in tools_response.tools:
                    try:
                        # 构建工具元数据
                        tool_info = {
                            "tool_name": tool.name,
                            "tool_description": tool.description or "",
                            "tool_params": []
                        }

                        # 解析参数模式
                        properties = tool.inputSchema.get("properties", {})
                        required_fields = tool.inputSchema.get("required", [])

                        for param_name, param_schema in properties.items():
                            tool_info["tool_params"].append({
                                "name": param_name,
                                "type": param_schema.get("type", "string"),
                                "description": param_schema.get("title", param_schema.get("description", "")),
                                "required": param_name in required_fields
                            })

                        # 构建 OpenAI 格式的 schema
                        openai_schema = {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        k: {
                                            "type": v.get("type", "string"),
                                            "description": v.get("title", v.get("description", ""))
                                        }
                                        for k, v in properties.items()
                                    },
                                    "required": required_fields
                                }
                            }
                        }

                        # 包装调用函数（绑定 server_name）
                        call_wrapper = partial(
                            self._call_tool_wrapper,
                            tool_name=tool.name,
                            target_server=server_name
                        )

                        # 注册到 ToolRegistry
                        self.tool_registry.function_info[tool.name] = tool_info
                        self.tool_registry.function_mappings[tool.name] = call_wrapper
                        self.tool_registry.openai_function_schemas.append(openai_schema)

                        registered_count += 1
                        print(f"   ✅ 已注册工具: {tool.name}")

                    except Exception as e:
                        print(f"   ⚠️ 注册工具 {tool.name} 失败: {e}")
                        continue

            except Exception as e:
                print(f"❌ 获取 Server [{server_name}] 工具列表失败: {e}")
                continue

        return registered_count > 0

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    async def _call_tool_wrapper(self, tool_name: str, target_server: str, **kwargs):
        """参数转换适配器（适配 ToolDispatcher 的调用方式）"""
        return await self.call_tool(
            tool_name=tool_name,
            arguments=kwargs,
            target_server=target_server
        )

    async def call_tool(self, tool_name: str, arguments: dict, target_server: str = None):
        """
        通用工具调用方法（持久连接版）。
        不再每次调用后销毁 session。
        """
        session = self.server_sessions.get(target_server)
        if not session:
            return {"error": f"MCP Server '{target_server}' 未连接"}

        try:
            result = await session.call_tool(tool_name, arguments)
            return {
                "server": target_server,
                "tool": tool_name,
                "result": result.content[0].text if result.content else "空结果"
            }
        except Exception as e:
            return {"error": f"MCP 工具调用失败 [{target_server}/{tool_name}]: {e}"}

    # ------------------------------------------------------------------
    # 参数校验
    # ------------------------------------------------------------------

    def _validate_arguments(self, arguments: dict, schema: dict):
        """简单参数校验"""
        required_fields = schema.get("required", [])
        for field in required_fields:
            if field not in arguments:
                raise ValueError(f"缺少必要参数: {field}")
