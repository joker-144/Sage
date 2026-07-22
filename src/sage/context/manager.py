"""
上下文管理器 — 统一管理对话历史、token 预算、上下文构建

AgentLoop 通过 ContextManager 与历史/工具结果交互，不直接操作消息列表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sage.config import get_config
from sage.context.history import ChatHistory, Message


class ContextManager:
    """上下文管理器 — 管理对话历史和 token 预算"""

    def __init__(
        self,
        workspace: Path,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        summary_trigger_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ):
        config = get_config()
        self.workspace = workspace
        self.system_prompt = system_prompt
        self.history = ChatHistory(
            max_tokens=max_tokens or config.max_context_tokens,
            summary_trigger_tokens=summary_trigger_tokens or config.summary_trigger_tokens,
            model=model or config.llm_chat_model,
        )

    def add_user_message(self, content: str):
        """添加用户输入"""
        self.history.add_user(content)

    def add_assistant_message(self, content: str, tool_calls: Optional[list[dict]] = None):
        """添加助手回复（可能含工具调用）"""
        self.history.add_assistant(content, tool_calls)

    def add_tool_result(self, tool_call_id: str, tool_name: str, result: str):
        """添加工具执行结果"""
        self.history.add_tool_result(tool_call_id, tool_name, result)

    def build_messages(self) -> list[dict[str, Any]]:
        """构建发送给 LLM 的完整消息列表"""
        return self.history.build_messages(self.system_prompt)

    async def maybe_compress(self, llm_client):
        """检查并触发摘要压缩（超过 token 阈值时）"""
        if self.history.needs_compression():
            await self.history.compress(llm_client)

    def token_count(self) -> int:
        """当前上下文的 token 数"""
        return self.history.token_count()
