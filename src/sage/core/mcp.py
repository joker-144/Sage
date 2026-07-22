"""
MCP (Model Context Protocol) 支持 — 标准化工具发现和调用协议

参考 Anthropic MCP 标准，实现:
- 工具发现（tool discovery）
- 标准化 tool schema
- 工具执行路由
- 资源管理

MCP 允许 Agent 动态发现和调用外部工具/资源，
而无需在代码中硬编码工具定义。

协议分层:
  - Transport: stdio / HTTP SSE
  - Protocol: JSON-RPC 风格请求/响应
  - Resources: 文件、数据库等资源访问
  - Tools: 工具发现与执行
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MCPTransport(Enum):
    """MCP 传输层"""
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


@dataclass
class MCPToolSchema:
    """MCP 工具定义 Schema"""
    name: str
    description: str
    parameters: dict = field(default_factory=dict)  # JSON Schema
    returns: dict = field(default_factory=dict)      # 返回值 Schema

    def to_openai_function(self) -> dict:
        """转为 OpenAI Function Calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters.get("properties", {}),
                    "required": self.parameters.get("required", []),
                },
            },
        }


@dataclass
class MCPResource:
    """MCP 资源定义"""
    uri: str           # 统一资源标识
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPRequest:
    """MCP 请求"""
    id: str
    method: str        # "tools/list" | "tools/call" | "resources/list"
    params: dict = field(default_factory=dict)


@dataclass
class MCPResponse:
    """MCP 响应"""
    id: str
    result: Any = None
    error: Optional[str] = None
    success: bool = True


class MCPToolProvider(ABC):
    """MCP 工具提供者抽象 — 任何实现此接口的对象都可注册为 MCP 工具"""

    @abstractmethod
    def list_tools(self) -> list[MCPToolSchema]:
        """列出可用工具"""
        ...

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> MCPResponse:
        """调用工具"""
        ...

    @abstractmethod
    def list_resources(self) -> list[MCPResource]:
        """列出可用资源"""
        ...


class LocalToolBridge(MCPToolProvider):
    """本地工具桥接 — 将 sage 现有工具适配为 MCP 协议

    现有 13 个工具通过此桥统一暴露为 MCP 格式，
    方便未来接入外部 MCP 生态。
    """

    def __init__(self, tool_engine=None):
        self._tool_engine = tool_engine
        self._tools_cache: Optional[list[MCPToolSchema]] = None

    def list_tools(self) -> list[MCPToolSchema]:
        """将 ToolEngine 中注册的工具转为 MCP Schema"""
        if self._tools_cache:
            return self._tools_cache

        if self._tool_engine is None:
            return []

        schemas = []
        for name, tool_def in self._tool_engine._tools.items():
            # tool_def 是 ToolDef dataclass，其 .schema 是 OpenAI function schema dict
            func_schema = tool_def.schema.get("function", {})
            schemas.append(MCPToolSchema(
                name=name,
                description=func_schema.get("description", ""),
                parameters=func_schema.get("parameters", {}),
            ))
        self._tools_cache = schemas
        return schemas

    async def call_tool(self, name: str, arguments: dict) -> MCPResponse:
        """通过 ToolEngine 执行工具"""
        if self._tool_engine is None:
            return MCPResponse(id="", error="ToolEngine 未初始化", success=False)

        try:
            # 构造一个临时 ToolCall 对象以适配 execute() 签名
            FakeToolCall = type("FakeToolCall", (), {
                "name": name,
                "arguments": arguments,
                "id": f"mcp_{name}",
            })
            result = await self._tool_engine.execute(FakeToolCall)
            return MCPResponse(
                id="",
                result={
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                    "summary": result.summary,
                },
            )
        except Exception as e:
            return MCPResponse(id="", error=str(e), success=False)

    def list_resources(self) -> list[MCPResource]:
        """列出本地资源（工作区可访问的文件/目录）"""
        return []  # 预留扩展


class MCPRegistry:
    """MCP 注册中心 — 管理多个工具提供者

    统一注册多个 MCP 适配器（本地工具、远程 MCP 服务、插件等），
    Agent 通过注册中心发现和调用工具。

    用法:
        registry = MCPRegistry()
        registry.register(LocalToolBridge(tool_engine))
        tools = registry.list_all_tools()
    """

    def __init__(self):
        self._providers: dict[str, MCPToolProvider] = {}
        self._tool_index: dict[str, str] = {}  # tool_name -> provider_name

    def register(self, name: str, provider: MCPToolProvider):
        """注册工具提供者"""
        self._providers[name] = provider
        # 重建工具索引
        for tool in provider.list_tools():
            self._tool_index[tool.name] = name

    def unregister(self, name: str):
        """取消注册"""
        if name in self._providers:
            del self._providers[name]
            # 清理索引
            self._tool_index = {
                tn: pn for tn, pn in self._tool_index.items()
                if pn != name
            }

    def list_all_tools(self) -> list[MCPToolSchema]:
        """列出所有已注册工具"""
        tools = []
        seen = set()
        for provider in self._providers.values():
            for tool in provider.list_tools():
                if tool.name not in seen:
                    tools.append(tool)
                    seen.add(tool.name)
        return tools

    def list_all_resources(self) -> list[MCPResource]:
        """列出所有已注册资源"""
        resources = []
        for provider in self._providers.values():
            resources.extend(provider.list_resources())
        return resources

    def get_openai_functions(self) -> list[dict]:
        """获取所有工具的 OpenAI Function Calling 格式"""
        return [t.to_openai_function() for t in self.list_all_tools()]

    async def call_tool(self, name: str, arguments: dict) -> MCPResponse:
        """调用工具（自动路由到正确的提供者）"""
        provider_name = self._tool_index.get(name)
        if not provider_name:
            return MCPResponse(id="", error=f"未找到工具: {name}", success=False)

        provider = self._providers.get(provider_name)
        if not provider:
            return MCPResponse(id="", error=f"工具提供者不可用: {provider_name}", success=False)

        return await provider.call_tool(name, arguments)

    def get_tool_schema(self, name: str) -> Optional[MCPToolSchema]:
        """获取指定工具的 Schema"""
        for tool in self.list_all_tools():
            if tool.name == name:
                return tool
        return None

    def stats(self) -> dict:
        """注册中心统计"""
        return {
            "providers": len(self._providers),
            "tools": len(self.list_all_tools()),
            "provider_names": list(self._providers.keys()),
        }


# ── 单例 ──

_mcp_registry: Optional[MCPRegistry] = None


def get_mcp_registry() -> MCPRegistry:
    """获取 MCP 注册中心单例"""
    global _mcp_registry
    if _mcp_registry is None:
        _mcp_registry = MCPRegistry()
    return _mcp_registry
