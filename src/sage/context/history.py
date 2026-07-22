"""
对话历史管理 — 多轮对话存储 + 摘要压缩

当对话历史超过 token 预算阈值时，自动触发 LLM 摘要压缩：
  - 保留最近 N 轮对话完整原文
  - 将较早的对话用 LLM 摘要为一段总结
  - 摘要后的历史以 system 消息形式注入上下文
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from sage.context.tokenizer import count_tokens, count_messages_tokens


@dataclass
class Message:
    """对话消息"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_call_id: str = ""  # role="tool" 时关联的 tool_call id
    name: str = ""  # role="tool" 时工具名

    def to_openai_dict(self) -> dict[str, Any]:
        """转为 OpenAI API 格式"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


class ChatHistory:
    """对话历史 — 支持 token 预算管理和摘要压缩"""

    def __init__(
        self,
        max_tokens: int = 60000,
        summary_trigger_tokens: int = 45000,
        model: str = "deepseek-chat",
    ):
        self.max_tokens = max_tokens
        self.summary_trigger_tokens = summary_trigger_tokens
        self.model = model
        self._messages: list[Message] = []
        self._summary: str = ""  # 压缩后的历史摘要

    @property
    def messages(self) -> list[Message]:
        return self._messages

    def add(self, message: Message):
        """添加消息"""
        self._messages.append(message)

    def add_user(self, content: str):
        """添加用户消息"""
        self.add(Message(role="user", content=content))

    def add_assistant(self, content: str, tool_calls: Optional[list[dict]] = None):
        """添加助手消息（可能含工具调用）"""
        self.add(Message(role="assistant", content=content, tool_calls=tool_calls or []))

    def add_tool_result(self, tool_call_id: str, name: str, content: str):
        """添加工具执行结果"""
        self.add(Message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        ))

    def token_count(self) -> int:
        """当前历史的总 token 数（含摘要）"""
        summary_tokens = count_tokens(self._summary, self.model) if self._summary else 0
        msg_tokens = count_messages_tokens(
            [m.to_openai_dict() for m in self._messages],
            self.model,
        )
        return summary_tokens + msg_tokens

    def needs_compression(self) -> bool:
        """是否需要触发摘要压缩"""
        return self.token_count() > self.summary_trigger_tokens

    async def compress(self, llm_client):
        """触发摘要压缩 — 将较早的对话用 LLM 摘要

        保留最近 6 条消息完整，更早的消息压缩为摘要。
        使用 asyncio.to_thread 避免阻塞事件循环。
        """
        if len(self._messages) <= 6:
            return  # 消息太少，无需压缩

        # 分割：较早的消息压缩，最近 6 条保留
        to_compress = self._messages[:-6]
        to_keep = self._messages[-6:]

        # 构建摘要请求
        history_text = "\n\n".join(
            f"[{m.role}] {m.content[:500]}"
            for m in to_compress
            if m.content
        )

        summary_prompt = (
            "请将以下对话历史压缩为一段简洁的摘要，保留关键信息：\n"
            "- 用户的真实需求和目标\n"
            "- 已经做出的决策和原因\n"
            "- 已经完成的操作和结果\n"
            "- 待解决的问题\n\n"
            f"对话历史:\n{history_text}"
        )

        try:
            # 使用 asyncio.to_thread 包装同步调用，避免阻塞事件循环
            new_summary = await asyncio.to_thread(
                llm_client.chat,
                [
                    {"role": "system", "content": "你是一个对话摘要助手，用中文简洁地总结对话。"},
                    {"role": "user", "content": summary_prompt},
                ],
                temperature=0.1,
                max_tokens=1024,
            )
            self._summary = new_summary
            self._messages = to_keep
        except Exception:
            # 摘要失败时保留原始消息，下次重试压缩
            pass

    def build_messages(self, system_prompt: str) -> list[dict[str, Any]]:
        """构建发送给 LLM 的完整消息列表

        结构: [system(含摘要)] + [历史消息]
        """
        messages: list[dict[str, Any]] = []

        # system prompt + 历史摘要
        full_system = system_prompt
        if self._summary:
            full_system += f"\n\n## 历史对话摘要\n{self._summary}"
        messages.append({"role": "system", "content": full_system})

        # 历史消息
        for m in self._messages:
            messages.append(m.to_openai_dict())

        return messages
