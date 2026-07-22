"""
AgentLoop — 核心智能体循环

2026 增强版 — 集成反思、可观测性、弹性重试、MCP 支持

工作循环:
  1. 构建 LLM 输入（system prompt + 上下文 + 工具定义）
  2. 调用 LLM（支持 function calling，异步不阻塞，带重试）
  3. 若 LLM 请求工具调用 → 执行工具 → 反思结果 → 必要时修正 → 加入上下文 → 继续循环
  4. 若 LLM 返回纯文本 → 任务完成，结束循环

LLM 自主决定：是否需要读文件？是否需要搜索代码？是否需要运行测试？
何时认为任务完成？这些决策不再是 Python 硬编码的 if/elif，而是 LLM 的推理结果。

v0.5.0 增强:
  - 反思机制：工具失败后自动分析原因并修正
  - 可观测性：完整的 Trace、Token 统计、工具调用记录
  - 弹性重试：LLM 调用失败自动指数退避重试
  - MCP 支持：通过 MCP 协议注册外部工具
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from sage.agent.system_prompt import get_system_prompt
from sage.agents.reflection import ReflectionEngine, ReflectionContext, create_reflection_engine
from sage.config import get_config
from sage.context.manager import ContextManager
from sage.core.observability import get_observability
from sage.core.resilience import (
    CircuitBreaker,
    RetryConfig,
    RetryExhaustedError,
    classify_error,
    ErrorSeverity,
    get_resilience_tracker,
)
from sage.llm.client import LLMClient
from sage.memory.store import get_store
from sage.tools.engine import ToolEngine


@dataclass
class LoopEvent:
    """AgentLoop 产生的事件（供 CLI/API 展示）"""
    type: str  # "tool_start" | "tool_result" | "text" | "error" | "done"
    content: str = ""
    tool_name: str = ""
    tool_args: dict = None
    tokens: dict = None      # 该轮 LLM 调用的 token 用量 {prompt, completion, total}
    skill_name: str = ""     # 当工具为技能调用时，记录使用的技能名

    def __post_init__(self):
        if self.tool_args is None:
            self.tool_args = {}
        if self.tokens is None:
            self.tokens = {}


class AgentLoop:
    """核心 Agent 循环 — LLM 自主决策执行路径 (v0.5.0 增强)

    每个实例维护独立的对话上下文，支持多轮对话。
    对话历史持久化到 SQLite（通过 MemoryStore）。

    v0.5.0 新增:
      - 反思引擎：工具执行后自动反思，失败自动修正
      - 可观测性：Trace + Token 统计 + 工具调用记录
      - 弹性重试：LLM 调用失败自动指数退避重试
      - 断路器：防止级联失败
    """

    def __init__(
        self,
        workspace: Optional[Path] = None,
        llm: Optional[LLMClient] = None,
        tools: Optional[ToolEngine] = None,
        context: Optional[ContextManager] = None,
        system_prompt: Optional[str] = None,
        conversation_id: Optional[str] = None,
        enable_reflection: bool = True,
        enable_observability: bool = True,
    ):
        config = get_config()
        self.workspace = workspace or config.workspace
        self.llm = llm or LLMClient()
        self.tools = tools or ToolEngine(self.workspace)
        self.system_prompt = system_prompt or get_system_prompt()

        # 注入已安装技能信息
        try:
            from sage.skill_system import SkillLoader
            skill_text = SkillLoader().format_for_prompt()
            if skill_text:
                self.system_prompt += skill_text
        except Exception:
            pass

        # v0.6.0 三层记忆注入
        try:
            from sage.memory.memory_orch import MemoryOrchestrator
            self._memory_orch = MemoryOrchestrator(enable_semantic=True)
        except Exception:
            self._memory_orch = None

        # 记忆上下文（含跨会话长期记忆 + 语义记忆）注入到 system prompt
        # 注意：此时还没有用户输入，只注入长期记忆（默认最重要的历史）
        if self._memory_orch:
            try:
                mem_text = self._memory_orch.get_memory_context()
                if mem_text:
                    self.system_prompt += mem_text
            except Exception:
                pass

        self.context = context or ContextManager(
            workspace=self.workspace,
            system_prompt=self.system_prompt,
        )
        self.max_tool_rounds = config.llm_chat_max_tool_rounds
        self.conversation_id = conversation_id

        # v0.5.0 新增：反思引擎
        self.enable_reflection = enable_reflection
        self.reflection_engine = create_reflection_engine(max_retries=2) if enable_reflection else None

        # v0.5.0 新增：可观测性
        self.enable_observability = enable_observability
        self.observability = get_observability() if enable_observability else None

        # v0.5.0 新增：弹性重试
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        self.resilience_tracker = get_resilience_tracker()

        # 初始化对话持久化
        self._init_conversation()

    def _init_conversation(self):
        """初始化对话记录到 SQLite"""
        try:
            store = get_store()
            if self.conversation_id is None:
                self.conversation_id = str(uuid.uuid4())
            store.create_conversation(self.conversation_id)
        except Exception:
            # 持久化失败不影响核心功能
            pass

    async def run(self, user_input: str) -> AsyncIterator[LoopEvent]:
        """运行 Agent 循环，流式输出事件 (v0.5.0 增强)

        增强: LLM 调用失败自动重试、工具执行后反思、可观测性统计

        Args:
            user_input: 用户输入

        Yields:
            LoopEvent: 工具调用、工具结果、文本回复等事件
        """
        session_id = self.conversation_id or str(uuid.uuid4())[:8]
        request_start = time.time()

        # 可观测性：初始化会话
        if self.observability:
            self.observability.init_session(session_id)

        self.context.add_user_message(user_input)
        self._persist_message("user", user_input)

        # v0.6.0 语义记忆注入：基于当前用户问题召回相关历史记忆
        if self._memory_orch:
            try:
                sem_text = self._memory_orch.get_memory_context(current_query=user_input)
                if sem_text:
                    # 将语义记忆追加到 system prompt 中，重新构建上下文
                    self.context.system_prompt = self.system_prompt + sem_text
            except Exception:
                pass

        # 重置反思状态（新任务开始）
        if self.reflection_engine:
            self.reflection_engine.reset()

        rounds = 0
        total_tool_calls = 0

        while rounds < self.max_tool_rounds:
            rounds += 1

            # 1. 检查 token 预算，必要时压缩
            await self.context.maybe_compress(self.llm)

            # 2. 构建 LLM 输入
            messages = self.context.build_messages()
            tool_schemas = self.tools.get_schemas()

            # 3. 调用 LLM（带弹性重试）
            llm_start = time.time()
            try:
                response = await self._call_llm_with_retry(
                    messages=messages,
                    tools=tool_schemas,
                    session_id=session_id,
                )
            except RetryExhaustedError as e:
                yield LoopEvent(type="error", content=f"LLM 调用重试耗尽: {e}")
                if self.observability:
                    self.observability.record_request(session_id, time.time() - request_start, error=True)
                return
            except Exception as e:
                yield LoopEvent(type="error", content=f"LLM 调用失败: {e}")
                if self.observability:
                    self.observability.record_request(session_id, time.time() - request_start, error=True)
                return

            llm_duration = (time.time() - llm_start) * 1000
            # 该轮 LLM 调用的 token 用量（供 tool_start 事件携带 + 持久化）
            round_usage = response.usage or {}
            # 可观测性：记录 LLM 调用
            if self.observability and round_usage:
                self.observability.record_llm_call(
                    session_id,
                    tokens_in=round_usage.get("prompt_tokens", 0),
                    tokens_out=round_usage.get("completion_tokens", 0),
                    duration_ms=llm_duration,
                )
            # 持久化 token 用量到 SQLite（供仪表盘统计）
            self._persist_token_usage(round_usage, session_id)

            # 4. 判断 LLM 是否要调用工具
            if response.has_tool_calls:
                # 记录助手消息（含 tool_calls）
                tool_calls_openai = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in response.tool_calls
                ]
                self.context.add_assistant_message(response.content, tool_calls_openai)
                self._persist_message("assistant", response.content, tool_calls_openai)

                # 该轮 token 用量（同一轮多个工具调用共享）
                round_tokens = {
                    "prompt": round_usage.get("prompt_tokens", 0),
                    "completion": round_usage.get("completion_tokens", 0),
                    "total": round_usage.get("prompt_tokens", 0)
                             + round_usage.get("completion_tokens", 0),
                }

                # 5. 逐个执行工具（带反思修正）
                for call in response.tool_calls:
                    # 技能调用识别：load_skill 代表 AI 决定使用某个技能
                    skill_name = ""
                    if call.name == "load_skill":
                        skill_name = call.arguments.get("name", "")

                    yield LoopEvent(
                        type="tool_start",
                        tool_name=call.name,
                        tool_args=call.arguments,
                        content=self._format_tool_call(call.name, call.arguments),
                        tokens=round_tokens,
                        skill_name=skill_name,
                    )

                    result = await self._execute_tool_with_reflection(
                        call=call,
                        session_id=session_id,
                    )

                    # 将结果加入上下文
                    self.context.add_tool_result(call.id, call.name, result.summary)
                    self._persist_message(
                        "tool", result.summary,
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )

                    yield LoopEvent(
                        type="tool_result",
                        tool_name=call.name,
                        content=result.summary,
                    )
                    total_tool_calls += 1

                continue
            else:
                # 6. LLM 返回最终文本回复，循环结束
                self.context.add_assistant_message(response.content)
                self._persist_message("assistant", response.content)
                yield LoopEvent(type="text", content=response.content)
                yield LoopEvent(type="done")

                # 可观测性：记录请求
                if self.observability:
                    self.observability.record_request(
                        session_id,
                        time.time() - request_start,
                    )
                    self.observability.record_tool_metrics(session_id, total_tool_calls)

                # v0.6.0 对话结束，保存会话摘要到长期记忆
                self._save_session_memory()
                return

        # 超过最大轮数 — 不直接报错，让 LLM 基于已有上下文生成一条总结性回复
        summary = await self._generate_limit_summary(session_id, request_start)
        if summary:
            self.context.add_assistant_message(summary)
            self._persist_message("assistant", summary)
            yield LoopEvent(type="text", content=summary)
        else:
            # LLM 总结失败时降级为友好提示（非 error，避免前端显示为报错）
            fallback = (
                f"已达到最大工具调用轮数（{self.max_tool_rounds} 轮），"
                "我已完成大部分工作但未能完全收尾。"
                "请告诉我是否需要继续，或检查上方已完成的工具调用结果。"
            )
            yield LoopEvent(type="text", content=fallback)
        yield LoopEvent(type="done")

        if self.observability:
            self.observability.record_request(session_id, time.time() - request_start)

        # v0.6.0 对话结束，保存会话摘要到长期记忆
        self._save_session_memory()

    async def _generate_limit_summary(self, session_id: str, request_start: float) -> str:
        """达到工具调用上限时，调用 LLM（不带工具）生成总结性回复

        让 AI 基于已完成的工具调用结果，给用户一个阶段性总结，
        而不是直接抛出"已达最大轮数"的报错。
        """
        try:
            messages = self.context.build_messages()
            # 追加一条提示，引导 LLM 收尾
            messages.append({
                "role": "user",
                "content": (
                    "（系统提示：已达到工具调用轮数上限，无法再调用工具。）"
                    "请基于已完成的操作和已获取的信息，给出当前阶段的总结与下一步建议。"
                ),
            })
            response = await self.llm.achat_with_tools(messages=messages, tools=[])
            # 持久化这次总结调用的 token
            if response.usage:
                self._persist_token_usage(response.usage, session_id)
            return response.content or ""
        except Exception:
            return ""

    def _persist_token_usage(self, usage: dict, session_id: str):
        """持久化 LLM 调用的 token 用量到 SQLite"""
        if not usage:
            return
        try:
            store = get_store()
            store.record_token_usage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                conversation_id=self.conversation_id or "",
                session_id=session_id,
                model=getattr(self.llm, "model", ""),
            )
        except Exception:
            pass

    def _save_session_memory(self):
        """保存当前对话到长期记忆（会话摘要 + 语义索引）"""
        if not self._memory_orch:
            return
        try:
            msgs = []
            for m in self.context.history.messages:
                msgs.append({
                    "role": m.role,
                    "content": m.content,
                    "tool_name": m.name if hasattr(m, "name") else "",
                })
            self._memory_orch.on_conversation_end(
                conversation_id=self.conversation_id,
                messages=msgs,
            )
        except Exception:
            pass

    async def _call_llm_with_retry(self, messages: list, tools: list, session_id: str):
        """带弹性重试的 LLM 调用

        使用 resilience.py 统一的 RetryConfig + CircuitBreaker 组合，
        指数退避公式和断路器逻辑与 retry_with_backoff 装饰器保持一致。
        """
        retry_cfg = RetryConfig(max_retries=3, base_delay=1.0, max_delay=30.0)
        last_error = None

        for attempt in range(retry_cfg.max_retries + 1):
            if self.circuit_breaker.is_open:
                raise RetryExhaustedError("断路器开启，拒绝 LLM 调用")

            try:
                response = await self.llm.achat_with_tools(
                    messages=messages,
                    tools=tools,
                )
                self.circuit_breaker.record_success()
                self.resilience_tracker.record("llm_call", success=True)
                return response

            except Exception as e:
                last_error = e
                severity = classify_error(e)
                self.circuit_breaker.record_failure()
                self.resilience_tracker.record("llm_call", success=False)

                if attempt >= retry_cfg.max_retries or severity in (ErrorSeverity.PERMANENT, ErrorSeverity.FATAL):
                    raise RetryExhaustedError(
                        f"LLM 调用重试 {retry_cfg.max_retries} 次后仍失败: {e}"
                    ) from e

                delay = min(
                    retry_cfg.base_delay * (retry_cfg.backoff_multiplier ** attempt),
                    retry_cfg.max_delay,
                )
                if retry_cfg.jitter:
                    import random
                    delay *= (0.5 + random.random())
                await asyncio.sleep(delay)

        raise RetryExhaustedError(f"LLM 调用异常: {last_error}") from last_error

    async def _execute_tool_with_reflection(self, call, session_id: str):
        """执行工具并触发反思修正（v0.5.0 新增）

        流程:
          1. 执行工具
          2. 记录结果到反思引擎
          3. 分析结果 → 决定是否需要修正
          4. 如需要且未超过重试上限 → 重新执行
        """
        tool_start = time.time()

        # 首次执行
        result = await self.tools.execute(call)
        tool_duration = (time.time() - tool_start) * 1000

        # 可观测性：记录工具调用
        if self.observability:
            self.observability.record_tool_call(
                tool_name=call.name,
                args=call.arguments,
                duration_ms=tool_duration,
                success=result.success,
                result_summary=result.summary if result.success else "",
                error=result.error if not result.success else "",
            )

        # 无反思引擎或结果成功 → 直接返回
        if not self.reflection_engine:
            return result

        # 记录上下文
        self.reflection_engine.record(ReflectionContext(
            step=len(self.reflection_engine._contexts) + 1,
            tool_name=call.name,
            tool_args=call.arguments,
            result=result.summary,
            success=result.success,
            error=result.error,
            timestamp=time.time(),
        ))

        # 反思分析
        reflection = self.reflection_engine.reflect(
            tool_name=call.name,
            result=result.summary,
            success=result.success,
            error=result.error,
        )

        if not reflection.needs_correction:
            # 如果不需修正但有建议，将建议注入到最后一条助理消息后
            if reflection.suggestion:
                self.context.add_assistant_message(
                    f"[反思建议] {reflection.suggestion}"
                )
            return result

        # 需要修正 → 最多重试 2 次
        for retry_idx in range(2):
            await asyncio.sleep(0.5 * (retry_idx + 1))  # 短暂等待

            retry_start = time.time()
            retry_result = await self.tools.execute(call)
            retry_duration = (time.time() - retry_start) * 1000

            if self.observability:
                self.observability.record_tool_call(
                    tool_name=f"{call.name}(retry{retry_idx + 1})",
                    args=call.arguments,
                    duration_ms=retry_duration,
                    success=retry_result.success,
                )

            if retry_result.success:
                return retry_result

        return result  # 返回最后一次结果

    def _persist_message(
        self,
        role: str,
        content: str,
        tool_calls: list[dict] = None,
        tool_call_id: str = "",
        tool_name: str = "",
    ):
        """持久化消息到 SQLite（失败不影响主流程）"""
        try:
            store = get_store()
            tool_args = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else ""
            store.add_message(
                conversation_id=self.conversation_id,
                role=role,
                content=content,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args=tool_args,
            )
            # 用首条用户消息自动设置对话标题
            if role == "user":
                store.update_conversation_title(self.conversation_id, content)
        except Exception:
            pass

    def _format_tool_call(self, name: str, args: dict) -> str:
        """格式化工具调用用于展示"""
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"[工具 {name}]({args_str})"


def create_agent(
    workspace: Optional[Path] = None,
    conversation_id: Optional[str] = None,
) -> AgentLoop:
    """创建 Agent 实例（工厂函数）

    所有 LLM 配置从 .env 读取（通过 config.get_config()）。
    """
    return AgentLoop(workspace=workspace, conversation_id=conversation_id)
