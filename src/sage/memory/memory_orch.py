"""
三层记忆编排器 — MemoryOrchestrator

统一管理三层记忆系统，提供 AgentLoop 使用的单点接口：

  1. 工作记忆 (Working Memory)
     - 当前对话消息（通过 ChatHistory / ContextManager）
     - 实时上下文（多轮对话窗口）
     - 摘要压缩（token 超限时自动压缩较早的对话）

  2. 长期记忆 (Long-Term Memory)
     - 跨会话摘要 — 所有过往对话的关键信息
     - 经验教训 — 过程中学到的重要经验
     - 用户偏好 — 用户的习惯和偏好
     - 按重要性排序

  3. 语义记忆 (Semantic Memory)
     - 记忆向量化 — 基于 Embedding 的语义检索
     - 跨会话关联 — 根据当前问题召回相关历史
     - 代码知识检索（复用 ProjectIndex）

工作流程:
  对话开始 → 召回长期记忆(重要性排序) + 语义记忆(基于首条消息)
          → 注入到 system prompt → Agent 可见历史知识
  对话结束 → 自动生成会话摘要 → 提取关键信息(自动化)
          → 存入长期记忆 + 语义索引
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from sage.memory.long_term import LongTermMemory
from sage.memory.semantic import SemanticMemory
from sage.memory.store import MemoryStore, get_store


class MemoryOrchestrator:
    """三层记忆编排器 — 提供 AgentLoop 使用的统一接口"""

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        enable_semantic: bool = True,
    ):
        self.store = store or get_store()
        self.long_term = LongTermMemory(self.store)
        self.semantic = SemanticMemory(self.store) if enable_semantic else None
        self.enable_semantic = enable_semantic

    # ── 对话生命周期 ──

    def get_memory_context(self, current_query: str = "") -> str:
        """为 system prompt 注入记忆上下文

        返回整合的文本块，包含长期记忆 + 语义记忆。
        """
        parts = []

        # 1. 长期记忆（跨会话摘要）
        lt_text = self.long_term.format_for_prompt()
        if lt_text:
            parts.append(lt_text)

        # 2. 语义记忆（基于当前问题的相关历史）
        if self.semantic and current_query:
            sem_text = self.semantic.format_for_prompt(current_query, top_k=3)
            if sem_text:
                parts.append(sem_text)

        return "\n".join(parts) if parts else ""

    def on_conversation_end(
        self,
        conversation_id: str,
        messages: list[dict],
        summary_text: str = "",
    ):
        """对话结束后自动提取记忆

        1. 生成会话摘要
        2. 提取关键决策、用户偏好
        3. 存入长期记忆 + 语义索引

        Args:
            conversation_id: 当前对话 ID
            messages: 对话消息列表（含角色和内容）
            summary_text: 可选的 LLM 生成的摘要
        """
        if not conversation_id or not messages:
            return

        # 提取关键信息
        extracted = self._extract_key_info(messages)
        summary = summary_text or extracted.get("summary", "")

        if not summary:
            return

        # 保存会话摘要
        self.long_term.save_session_summary(
            conversation_id=conversation_id,
            summary=summary,
            key_decisions=extracted.get("key_decisions", ""),
            user_preferences=extracted.get("user_preferences", ""),
            completed_tasks=extracted.get("completed_tasks", ""),
            unresolved_items=extracted.get("unresolved_items", ""),
            tech_stack=extracted.get("tech_stack", ""),
        )

        # 存储经验教训到向量索引
        memories = []
        for key, val in extracted.items():
            if key in ("lessons", "preferences") and val:
                if isinstance(val, str):
                    items = [v.strip() for v in val.split("\n") if v.strip()]
                else:
                    items = val if isinstance(val, list) else []
                for item in items[:5]:
                    mem_type = "lesson" if key == "lessons" else "preference"
                    memories.append({
                        "content": item,
                        "type": mem_type,
                        "conv_id": conversation_id,
                        "importance": 0.6,
                    })

        # 存储关键决策
        decisions = extracted.get("key_decisions", "")
        if decisions:
            for d in decisions.split("\n"):
                d = d.strip()
                if d:
                    memories.append({
                        "content": d,
                        "type": "decision",
                        "conv_id": conversation_id,
                        "importance": 0.8,
                    })

        if memories and self.semantic:
            self.semantic.store_batch(memories)
            self.semantic.invalidate_cache()

    def _extract_key_info(self, messages: list[dict]) -> dict:
        """从对话消息中提取关键信息（启发式规则）"""
        result = {
            "summary": "",
            "key_decisions": "",
            "user_preferences": "",
            "completed_tasks": "",
            "unresolved_items": "",
            "tech_stack": "",
            "lessons": [],
        }

        assistant_lines = []
        decision_points = []
        preferences = []
        tasks_done = []
        lessons = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_name = msg.get("tool_name", "")

            if role == "user" and len(content) > 15:
                # 用户长消息可能包含需求描述
                if any(kw in content for kw in ["用", "配置", "setup", "项目", "技术", "react", "vue", "python"]):
                    if "技术" in content or "用" in content:
                        result["tech_stack"] = content[:200]

            elif role == "assistant":
                assistant_lines.append(content[:300])

                # 提取决策点
                if any(kw in content for kw in ["决定", "选择", "采用", "最终方案", "我建议"]):
                    decision_points.append(content[:200])

                # 提取经验教训
                if any(kw in content for kw in ["发现", "注意", "重要", "建议", "最佳实践"]):
                    lessons.append(content[:200])

            elif role == "tool" and tool_name:
                # 工具调用 — 标记为已完成任务的一部分
                if tool_name in ("write_file", "edit_file", "run_command"):
                    tasks_done.append(f"[{tool_name}] {content[:150]}")

        # 摘要：取助理回复的前 3 条非空内容
        summary_lines = [l for l in assistant_lines if l][:3]
        result["summary"] = "\n".join(summary_lines) if summary_lines else ""
        result["key_decisions"] = "\n".join(decision_points[:3])
        result["completed_tasks"] = "\n".join(tasks_done[:5])
        result["lessons"] = lessons[:3]

        return result

    # ── 统计 ──

    def stats(self) -> dict:
        """记忆系统整体统计"""
        base = self.store.stats()
        base["memory_system"] = "three_tier"
        base["working_memory"] = {"enabled": True, "type": "ChatHistory"}
        base["long_term_memory"] = {"enabled": True, "entries": base["session_summaries"]}
        base["semantic_memory"] = {
            "enabled": self.enable_semantic,
            "entries": base["memory_embeddings"],
        }
        return base


def create_memory_orchestrator(enable_semantic: bool = True) -> MemoryOrchestrator:
    return MemoryOrchestrator(enable_semantic=enable_semantic)
