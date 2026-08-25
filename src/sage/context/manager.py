"""
上下文管理器 — 统一管理对话历史、token 预算、上下文构建

AgentLoop 通过 ContextManager 与历史/工具结果交互，不直接操作消息列表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sage.config import get_config
from sage.context.history import ChatHistory, Message
from sage.context.model_limits import get_compression_trigger
from sage.context.tokenizer import count_tokens


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
        # 当前对话使用的模型：优先显式传入，否则取 .env 配置的对话模型
        model_id = model or config.llm_chat_model
        # 压缩触发阈值随模型动态化：按模型上下文窗口 × 80% 计算，
        # 未命中映射表时回退配置默认值；显式传入的 summary_trigger_tokens 仍优先。
        dynamic_trigger = get_compression_trigger(model_id, config.summary_trigger_tokens)
        self.history = ChatHistory(
            max_tokens=max_tokens or config.max_context_tokens,
            summary_trigger_tokens=summary_trigger_tokens or dynamic_trigger,
            model=model_id,
        )
        # 压缩统计（供前端上下文使用指示器展示）
        # rounds: 累计压缩轮数; saved_tokens: 累计节省 token; last_saved: 最近一次压缩节省量
        self.compression_stats = {"rounds": 0, "saved_tokens": 0, "last_saved": 0}

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
        """检查并触发摘要压缩（超过 token 阈值时）

        system prompt 纳入 token 预算计算，避免长 system prompt
        （含技能/记忆注入）导致实际上下文超限却未触发压缩。
        """
        if self._total_token_count() > self.history.summary_trigger_tokens:
            before = self._total_token_count()
            await self.history.compress(llm_client)
            after = self._total_token_count()
            # 压缩有效（token 减少）时更新统计；失败时保持原值下次重试
            if after < before:
                saved = max(before - after, 0)
                self.compression_stats["rounds"] += 1
                self.compression_stats["saved_tokens"] += saved
                self.compression_stats["last_saved"] = saved

    def _system_prompt_tokens(self) -> int:
        """system prompt 的 token 数"""
        return count_tokens(self.system_prompt, self.history.model) if self.system_prompt else 0

    def _total_token_count(self) -> int:
        """含 system prompt 的上下文总 token 数"""
        return self.history.token_count() + self._system_prompt_tokens()

    def token_count(self) -> int:
        """当前上下文的 token 数（含 system prompt）"""
        return self._total_token_count()
