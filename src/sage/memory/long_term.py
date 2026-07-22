"""
长期记忆系统 — Long-Term Memory

功能:
  1. 跨会话摘要管理 — 每条对话结束后自动生成摘要
  2. 关键知识提取 — 从对话中提取决策、用户偏好、技术栈
  3. 记忆召回 — 在 system prompt 中注入相关历史摘要
  4. 重要性评分 — 高频访问的记忆自动提升重要性

与工作记忆的区别:
  - 工作记忆: 当前对话的完整消息列表（实时上下文）
  - 长期记忆: 所有过往对话的摘要 + 经验教训（持久化 SQLite）
"""
from __future__ import annotations

import json
import time
from typing import Optional

from sage.memory.store import MemoryStore, get_store


class LongTermMemory:
    """长期记忆管理器 — 跨会话记忆

    每次对话结東后自动生成会话摘要存入 SQLite，
    新对话开始时注入最相关的历史摘要到 system prompt。
    """

    # 记忆类型
    TYPE_LESSON = "lesson"
    TYPE_PREFERENCE = "preference"
    TYPE_DECISION = "decision"
    TYPE_TASK = "task"
    TYPE_TECH_STACK = "tech_stack"

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store or get_store()

    # ── 会话摘要管理 ──

    def save_session_summary(
        self,
        conversation_id: str,
        summary: str,
        key_decisions: str = "",
        user_preferences: str = "",
        completed_tasks: str = "",
        unresolved_items: str = "",
        tech_stack: str = "",
    ) -> int:
        """保存对话会话摘要"""
        return self.store.save_session_summary(
            conversation_id=conversation_id,
            summary=summary,
            key_decisions=key_decisions,
            user_preferences=user_preferences,
            completed_tasks=completed_tasks,
            unresolved_items=unresolved_items,
            tech_stack=tech_stack,
        )

    def get_recent_summaries(self, limit: int = 10) -> list[dict]:
        """获取最近 N 条会话摘要（不含当前会话）"""
        return self.store.get_session_summaries(limit=limit)

    # ── 记忆存储与召回 ──

    def store_memory(
        self,
        content: str,
        memory_type: str = "lesson",
        conversation_id: str = "",
        embedding: bytes = b"",
        importance: float = 0.5,
    ) -> int:
        """存储一条记忆（可选带向量）"""
        return self.store.store_memory_embedding(
            content=content,
            memory_type=memory_type,
            conversation_id=conversation_id,
            embedding=embedding,
            importance=importance,
        )

    def recall_important_memories(self, limit: int = 8) -> list[dict]:
        """召回最重�的记忆（按重要性 + 访问频率排序）

        用于注入 system prompt，让 AI 记住关键历史信息。
        """
        return self.store.query_memories_by_importance(limit=limit)

    def recall_by_type(self, memory_type: str, limit: int = 5) -> list[dict]:
        """按类型召回记忆"""
        rows = self.store.conn.execute(
            "SELECT content, importance, created_at FROM memory_embeddings "
            "WHERE memory_type = ? ORDER BY importance DESC, access_count DESC LIMIT ?",
            (memory_type, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_importance(self, content_keyword: str, delta: float = 0.1):
        """当记忆被成功引用时提升重要性"""
        self.store.conn.execute(
            "UPDATE memory_embeddings SET importance = MIN(1.0, importance + ?) "
            "WHERE content LIKE ?",
            (delta, f"%{content_keyword}%"),
        )
        self.store.conn.commit()

    # ── 格式化输出 ──

    def format_for_prompt(self) -> str:
        """生成供 system prompt 注入的长期记忆文本

        结构:
          - 用户偏好 (preference)
          - 过往决策 (decision)
          - 已完成任务 (task)
          - 重要经验教训 (lesson)
        """
        memories = self.recall_important_memories(limit=8)
        if not memories:
            return ""

        lines = ["\n## 长期记忆（跨会话）\n"]

        # 按类型分组
        categorized = {
            "preference": [],
            "decision": [],
            "task": [],
            "lesson": [],
        }
        for m in memories:
            mtype = m.get("memory_type", "lesson")
            if mtype in categorized:
                categorized[mtype].append(m["content"])

        labels = {
            "preference": "用户偏好",
            "decision": "关键决策",
            "task": "已完成任务",
            "lesson": "经验教训",
        }
        for mtype, label in labels.items():
            items = categorized[mtype]
            if items:
                lines.append(f"\n**{label}:**")
                for item in items[:3]:
                    lines.append(f"- {item[:200]}")

        lines.append("\n*这些记忆来源于历史对话，AI 可据此保持行为一致性。*")
        return "\n".join(lines) + "\n"


def create_long_term_memory() -> LongTermMemory:
    return LongTermMemory()
