"""
LLM 客户端 — 支持 function calling + streaming

核心能力:
  - chat_with_tools(): 支持 OpenAI tools/tool_choice 参数，返回含 tool_calls 的完整 message
  - achat_with_tools(): 异步版本（不阻塞事件循环）
  - chat_stream(): 流式输出，实时返回生成内容
  - chat(): 基础文本对话
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx
from openai import AsyncOpenAI, OpenAI

from sage.config import get_config


@dataclass
class ToolCall:
    """LLM 请求的工具调用"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    """LLM 返回的完整消息（含工具调用）"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient:
    """LLM 客户端 — 支持 function calling + streaming

    通过 OpenAI 兼容协议接入 DeepSeek / Qwen / OpenAI 等服务商
    """

    def __init__(self, *, api_key: str = "", base_url: str = "", model: str = "",
                 temperature: float | None = None, max_tokens: int | None = None,
                 timeout: float | None = None):
        config = get_config()
        self.model = model or config.llm_chat_model
        self.temperature = temperature if temperature is not None else config.llm_chat_temperature
        self.max_tokens = max_tokens if max_tokens is not None else config.llm_chat_max_tokens
        _api_key = api_key or config.llm_chat_api_key
        _base_url = base_url or config.llm_chat_base_url
        _timeout = timeout if timeout is not None else config.llm_chat_timeout
        self._api_key = _api_key
        self._base_url = _base_url
        # 同步客户端（用于简单调用 / 摘要）
        self._sync = OpenAI(
            api_key=_api_key,
            base_url=_base_url,
            timeout=_timeout,
        )
        # 异步客户端（用于 AgentLoop，不阻塞事件循环）
        # DeepSeek 官方推荐: connect=10s, read=60s, write=30s
        # read=60s 确保非流式 tool_calling 响应有足够时间（复杂推理 + 多工具调用）
        _stream_timeout = httpx.Timeout(
            connect=10.0,   # 建立 TCP 连接
            read=60.0,      # 响应读取超时（流式: 逐chunk; 非流式: 完整响应）
            write=30.0,     # 发送请求体
            pool=10.0,      # 从连接池获取连接
        )
        self._async = AsyncOpenAI(
            api_key=_api_key,
            base_url=_base_url,
            timeout=_stream_timeout,
        )

    # ── 基础文本对话 ──

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """基础对话，返回纯文本（同步）"""
        response = self._sync.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        return response.choices[0].message.content or ""

    # ── Function Calling（异步，不阻塞事件循环）──

    async def achat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatMessage:
        """异步 function calling 对话 — 不阻塞事件循环

        Args:
            messages: 对话消息列表
            tools: OpenAI function calling schema 列表
            tool_choice: "auto" | "none" | {"type": "function", "function": {"name": "..."}}

        Returns:
            ChatMessage: 含 content 和 tool_calls 的完整消息
        """
        response = await self._async.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )

        return self._parse_response(response)

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatMessage:
        """同步 function calling 对话（保留兼容，但 AgentLoop 应使用 achat_with_tools）"""
        response = self._sync.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )

        return self._parse_response(response)

    def _parse_response(self, response) -> ChatMessage:
        """解析 OpenAI 响应为 ChatMessage（统一处理 tool_call.id 为 None 的情况）"""
        msg = response.choices[0].message
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for idx, call in enumerate(msg.tool_calls):
                try:
                    args = json.loads(call.function.arguments) if call.function.arguments else {}
                except json.JSONDecodeError:
                    args = {"_raw": call.function.arguments}
                # 某些 Provider 可能返回 None id，生成回退 id
                call_id = call.id if call.id else f"call_{idx}"
                tool_calls.append(ToolCall(
                    id=call_id,
                    name=call.function.name,
                    arguments=args,
                ))

        # 提取 usage（prompt_tokens / completion_tokens）
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            }

        return ChatMessage(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=response.choices[0].finish_reason,
            usage=usage,
        )

    # ── 流式输出 ──

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """流式输出 — 实时返回生成内容

        Yields:
            文本片段（delta content）
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        stream = await self._async.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def create_llm_client() -> LLMClient:
    """创建 LLM 客户端（工厂函数）"""
    return LLMClient()
