"""
记忆层 — SQLite 统一存储

存储内容:
  - conversations: 对话会话
  - messages: 对话消息（含工具调用）
  - session_summaries: 跨会话摘要（长期记忆基础）
  - memory_embeddings: 记忆向量索引（语义检索）
  - file_index: 代码库文件索引（含 Embedding 向量）
  - lessons: 经验教训（已废弃，由 memory_embeddings 替代）

三层记忆架构:
  - 工作记忆 (Working Memory): 当前对话消息 + 实时上下文（内存）
  - 长期记忆 (Long-term Memory): 跨会话摘要 + 经验教训 + 用户偏好（SQLite）
  - 语义记忆 (Semantic Memory): 对话 + 代码向量的语义检索（SQLite + Embedding）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from sage.config import get_config


# ── Schema DDL ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_call_id TEXT,
    tool_name TEXT,
    tool_args TEXT,
    tokens INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- 会话摘要 — 每条对话结束后自动生成，供长期记忆跨会话召回
CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    key_decisions TEXT,
    user_preferences TEXT,
    completed_tasks TEXT,
    unresolved_items TEXT,
    tech_stack TEXT,
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- 记忆向量索引 — 统一存储经验和知识的 Embedding 向量
CREATE TABLE IF NOT EXISTS memory_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    memory_type TEXT NOT NULL DEFAULT 'lesson',
    content TEXT NOT NULL,
    embedding BLOB,
    metadata TEXT,
    importance REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP
);

CREATE TABLE IF NOT EXISTS file_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    content TEXT NOT NULL,
    embedding BLOB,
    file_hash TEXT,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tags TEXT,
    embedding BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Token 用量记录 — 每次 LLM 调用持久化，供仪表盘统计
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    session_id TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    model TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_file_index_path ON file_index(file_path);
CREATE INDEX IF NOT EXISTS idx_session_summaries_conv ON session_summaries(conversation_id);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_type ON memory_embeddings(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_importance ON memory_embeddings(importance DESC);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at);
"""


class MemoryStore:
    """SQLite 统一存储 — 线程安全的单例

    注意：单例以第一次初始化的 db_path 为准。
    如需切换数据库（如测试），调用 reset_store() 重置单例。
    """

    _instance: Optional["MemoryStore"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        if self._initialized:
            return
        config = get_config()
        self.db_path = db_path or config.memory_sqlite_path
        # 确保目录存在
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        self._initialized = True
        # 迁移：检测旧向量维度，不匹配时自动清空（智谱 1024维 → 本地 384维）
        self._migrate_embedding_dim()

    def _migrate_embedding_dim(self):
        """检测并清理维度不匹配的旧向量数据

        智谱 Embedding-3 为 1024 维，本地 all-MiniLM-L6-v2 为 384 维。
        首次启动时自动检测并清空不兼容的向量，避免搜索时维度冲突。
        """
        try:
            # 检查 file_index 表
            row = self._conn.execute(
                "SELECT embedding FROM file_index WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
            if row and row["embedding"] is not None:
                dim = len(row["embedding"]) // 4  # float32 = 4 bytes
                if dim != 384:
                    self._conn.execute("UPDATE file_index SET embedding = NULL")
                    self._conn.execute("UPDATE file_index SET file_hash = NULL")
                    self._conn.commit()

            # 检查 memory_embeddings 表
            row = self._conn.execute(
                "SELECT embedding FROM memory_embeddings WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
            if row and row["embedding"] is not None:
                dim = len(row["embedding"]) // 4
                if dim != 384:
                    self._conn.execute("UPDATE memory_embeddings SET embedding = NULL")
                    self._conn.commit()
        except Exception:
            pass

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ── 对话 ──

    def create_conversation(self, conv_id: str, title: str = "") -> None:
        """创建新对话"""
        self._conn.execute(
            "INSERT OR IGNORE INTO conversations (id, title, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (conv_id, title),
        )
        self._conn.commit()

    def update_conversation_title(self, conv_id: str, title: str) -> None:
        """只在标题为空时设置标题（取用户首条消息的前30字符）"""
        title = title[:30].replace("\n", " ").strip()
        if len(title) > 15:
            title = title[:15] + "..."
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND (title IS NULL OR title = '')",
            (title, conv_id),
        )
        self._conn.commit()

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_call_id: str = "",
        tool_name: str = "",
        tool_args: str = "",
        tokens: int = 0,
    ) -> int:
        """添加消息，返回消息 id"""
        cur = self._conn.execute(
            """INSERT INTO messages
               (conversation_id, role, content, tool_call_id, tool_name, tool_args, tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, role, content, tool_call_id or None,
             tool_name or None, tool_args or None, tokens),
        )
        # 同步更新对话的 updated_at，让侧栏按最近活跃度排序
        self._conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_conversations(self, limit: int = 50) -> list[dict]:
        """获取对话列表（按更新时间倒序）"""
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_messages(self, conversation_id: str, limit: int = 100) -> list[dict]:
        """获取对话的消息列表"""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话及其所有消息"""
        self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        self._conn.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        self._conn.commit()
        return True

    # ── 会话摘要（长期记忆）──

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
        cur = self._conn.execute(
            """INSERT INTO session_summaries
               (conversation_id, summary, key_decisions, user_preferences,
                completed_tasks, unresolved_items, tech_stack)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, summary, key_decisions or None,
             user_preferences or None, completed_tasks or None,
             unresolved_items or None, tech_stack or None),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_session_summaries(self, limit: int = 20) -> list[dict]:
        """获取最近 N 条会话摘要"""
        rows = self._conn.execute(
            "SELECT * FROM session_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 记忆向量索引（语义检索）──

    def store_memory_embedding(
        self,
        content: str,
        memory_type: str = "lesson",
        conversation_id: str = "",
        embedding: bytes = b"",
        metadata: str = "",
        importance: float = 0.5,
    ) -> int:
        """存储一条带 Embedding 的记忆"""
        cur = self._conn.execute(
            """INSERT INTO memory_embeddings
               (conversation_id, memory_type, content, embedding, metadata, importance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conversation_id or None, memory_type, content,
             embedding or None, metadata or None, importance),
        )
        self._conn.commit()
        return cur.lastrowid

    def load_all_memory_embeddings(self, memory_type: str = "") -> list[dict]:
        """加载所有记忆向量"""
        if memory_type:
            rows = self._conn.execute(
                "SELECT * FROM memory_embeddings WHERE memory_type = ? AND embedding IS NOT NULL",
                (memory_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memory_embeddings WHERE embedding IS NOT NULL"
            ).fetchall()
        return [dict(r) for r in rows]

    def query_memories_by_importance(self, limit: int = 10) -> list[dict]:
        """按重要性查询记忆（用于 prompt 注入）"""
        rows = self._conn.execute(
            "SELECT content, memory_type, importance FROM memory_embeddings "
            "ORDER BY importance DESC, access_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_memory_access(self, mem_id: int):
        """增加记忆访问次数"""
        self._conn.execute(
            "UPDATE memory_embeddings SET access_count = access_count + 1, "
            "last_accessed = CURRENT_TIMESTAMP WHERE id = ?",
            (mem_id,),
        )
        self._conn.commit()

    # ── 文件索引 ──

    def store_chunk(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        content: str,
        embedding: bytes,
        file_hash: str,
    ) -> None:
        """存储代码块（含向量）"""
        self._conn.execute(
            """INSERT INTO file_index
               (file_path, start_line, end_line, content, embedding, file_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_path, start_line, end_line, content, embedding, file_hash),
        )
        self._conn.commit()

    def delete_file_chunks(self, file_path: str) -> None:
        """删除指定文件的所有索引块（用于重新索引前清理）"""
        self._conn.execute(
            "DELETE FROM file_index WHERE file_path = ?",
            (file_path,),
        )
        self._conn.commit()

    def get_file_hash(self, file_path: str) -> Optional[str]:
        """获取文件已索引的 hash（用于增量更新判断）"""
        row = self._conn.execute(
            "SELECT file_hash FROM file_index WHERE file_path = ? LIMIT 1",
            (file_path,),
        ).fetchone()
        return row["file_hash"] if row else None

    def load_all_embeddings(self) -> list[dict]:
        """加载所有代码块向量（供向量搜索）

        Returns:
            list of {"id", "file_path", "start_line", "end_line", "content", "embedding"}
        """
        rows = self._conn.execute(
            "SELECT id, file_path, start_line, end_line, content, embedding FROM file_index"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 经验教训 ──

    def add_lesson(self, content: str, tags: str = "", embedding: bytes = b"") -> int:
        """添加经验教训"""
        cur = self._conn.execute(
            "INSERT INTO lessons (content, tags, embedding) VALUES (?, ?, ?)",
            (content, tags, embedding),
        )
        self._conn.commit()
        return cur.lastrowid

    # ── Token 用量 ──

    def record_token_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        conversation_id: str = "",
        session_id: str = "",
        model: str = "",
    ) -> int:
        """记录一次 LLM 调用的 token 用量"""
        total = (prompt_tokens or 0) + (completion_tokens or 0)
        cur = self._conn.execute(
            """INSERT INTO token_usage
               (conversation_id, session_id, prompt_tokens, completion_tokens, total_tokens, model)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conversation_id or None, session_id or None,
             prompt_tokens or 0, completion_tokens or 0, total, model or None),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_token_stats(self) -> dict:
        """获取 Token 用量统计（总量 + 今日）"""
        # 总量
        row = self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0) AS p, "
            "COALESCE(SUM(completion_tokens),0) AS c, "
            "COALESCE(SUM(total_tokens),0) AS t, "
            "COUNT(*) AS n FROM token_usage"
        ).fetchone()
        # 今日（SQLite CURRENT_TIMESTAMP 为 UTC，这里用 date() 比对）
        today = self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0) AS p, "
            "COALESCE(SUM(completion_tokens),0) AS c, "
            "COALESCE(SUM(total_tokens),0) AS t, "
            "COUNT(*) AS n FROM token_usage "
            "WHERE date(created_at) = date('now')"
        ).fetchone()
        return {
            "total_prompt": row["p"],
            "total_completion": row["c"],
            "total_tokens": row["t"],
            "total_calls": row["n"],
            "today_prompt": today["p"],
            "today_completion": today["c"],
            "today_tokens": today["t"],
            "today_calls": today["n"],
        }

    # ── 统计 ──

    def stats(self) -> dict[str, Any]:
        """获取记忆系统统计"""
        conv_count = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        msg_count = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        tool_count = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE tool_name IS NOT NULL AND tool_name != ''"
        ).fetchone()[0]
        summary_count = self._conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
        mem_emb_count = self._conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        chunk_count = self._conn.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        lesson_count = self._conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        return {
            "conversations": conv_count,
            "messages": msg_count,
            "tool_calls": tool_count,
            "session_summaries": summary_count,
            "memory_embeddings": mem_emb_count,
            "file_chunks": chunk_count,
            "lessons": lesson_count,
            "db_path": self.db_path,
        }


def get_store() -> MemoryStore:
    """获取 MemoryStore 单例"""
    return MemoryStore()


def reset_store():
    """重置 MemoryStore 单例（用于测试或切换数据库）"""
    with MemoryStore._lock:
        if MemoryStore._instance is not None:
            try:
                MemoryStore._instance._conn.close()
            except Exception:
                pass
        MemoryStore._instance = None
