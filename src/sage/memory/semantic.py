"""
语义记忆系统 — Semantic Memory

基于向量检索的知识召回系统。
与长期记忆（按重要性排序）互补，语义记忆按语义相似度召回。

功能:
  1. 记忆向量化 — 将关键记忆转为 Embedding 向量存入 SQLite
  2. 语义检索 — 给定用户查询，召回语义最相似的过往记忆
  3. 跨会话关联 — 根据当前问题自动召回相关历史会话片段
  4. 与 ProjectIndex 共享本地 Embedding 引擎

使用场景:
  - 用户说"我之前是怎么配置的" → 召回相关历史片段
  - 用户提出技术问题 → 召回过往类似问题的解决经验
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import numpy as np

from sage.memory.store import MemoryStore, get_store

logger = logging.getLogger(__name__)


class SemanticMemory:
    """语义记忆管理器 — 基于 Embedding 的智能检索"""

    # 模块级共享向量缓存：多个 SemanticMemory 实例（每个 Agent 一个）共用同一份
    # 只读矩阵，避免每实例重复从 SQLite 反序列化全量向量（内存冗余 + 检索低效）
    _SHARED_CACHE: dict[str, list | None] = {}

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store or get_store()
        self._embedder = None
        # 以 db_path 为键共享：指向 _SHARED_CACHE 中当前值（None 表示未构建）
        self._cache_key = str(getattr(self.store, "db_path", ""))
        self._shared = SemanticMemory._SHARED_CACHE.setdefault(self._cache_key, None)

    @property
    def embedder(self):
        """延迟创建 Embedder（复用 context.index 中的本地 Embedder）"""
        if self._embedder is None:
            from sage.context.index import LocalEmbedder
            self._embedder = LocalEmbedder()
        return self._embedder

    # ── 记忆写入 ──

    def store(
        self,
        content: str,
        memory_type: str = "lesson",
        conversation_id: str = "",
        importance: float = 0.5,
    ) -> int:
        """存储一条记忆（自动生成 Embedding 向量）

        Args:
            content: 记忆内容
            memory_type: lesson | preference | decision | task | tech_stack
            conversation_id: 关联对话 ID
            importance: 初始重要性 0-1
        """
        try:
            embedding = self.embedder.encode([content])[0]
            emb_bytes = embedding.tobytes()
        except Exception as e:
            logger.warning("语义记忆向量化失败，将以无向量形式存储: %s", e)
            emb_bytes = b""

        return self.store.store_memory_embedding(
            content=content,
            memory_type=memory_type,
            conversation_id=conversation_id,
            embedding=emb_bytes,
            metadata=json.dumps({"auto_extracted": True}),
            importance=importance,
        )

    def store_batch(self, memories: list[dict]) -> list[int]:
        """批量存储多条记忆

        Args:
            memories: [{"content": str, "type": str, "conv_id": str, "importance": float}]
        """
        if not memories:
            return []

        texts = [m["content"] for m in memories]
        try:
            embeddings = self.embedder.encode(texts)
        except Exception as e:
            logger.warning("批量记忆向量化失败，将以无向量形式存储: %s", e)
            embeddings = [np.array([], dtype=np.float32)] * len(texts)

        ids = []
        for i, m in enumerate(memories):
            emb_bytes = embeddings[i].tobytes() if embeddings[i].size > 0 else b""
            mid = self.store.store_memory_embedding(
                content=m["content"],
                memory_type=m.get("type", "lesson"),
                conversation_id=m.get("conv_id", ""),
                embedding=emb_bytes,
                importance=m.get("importance", 0.5),
            )
            ids.append(mid)
        return ids

    # ── 语义检索 ──

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """语义搜索记忆

        根据 query 语义召回最相关的历史记忆。

        向量化实现：查询向量预归一化 + 全库向量矩阵单次矩阵乘，
        替代原先逐行 np.dot/norm 的 Python 循环（记忆量大时慢数量级）。

        Args:
            query: 当前用户问题的原始文本（内部会先转为 Embedding 向量再检索）
            top_k: 返回条数
        """
        try:
            query_vec = self.embedder.encode([query])[0].astype(np.float32)
        except Exception as e:
            logger.warning("语义记忆检索向量化失败: %s", e)
            return []
        qnorm = np.linalg.norm(query_vec)
        if qnorm > 0:
            query_vec = query_vec / qnorm

        self._ensure_cache(query_vec.shape[0])
        mat, meta = self._shared
        if mat is None or mat.shape[0] == 0:
            return []

        # 单次矩阵乘：所有记忆与查询的余弦相似度
        scores = mat @ query_vec
        # 加权：语义相似度 × 重要性（与原逐行实现语义一致）
        weights = np.array(
            [0.7 + 0.3 * float(m.get("importance", 0.5)) for m in meta],
            dtype=np.float32,
        )
        weighted = scores * weights

        idx = np.argsort(-weighted)[:top_k]
        scored = []
        for i in idx:
            row = meta[i]
            scored.append({
                "id": row["id"],
                "content": row["content"],
                "memory_type": row["memory_type"],
                "conversation_id": row["conversation_id"],
                "importance": row.get("importance", 0.5),
                "score": round(float(weighted[i]), 4),
            })
        return scored

    def _ensure_cache(self, dim: int):
        """确保共享向量缓存已构建且维度匹配（不匹配时重建）

        共享只读矩阵：所有实例共用一份，避免每次搜索重复反序列化全量向量。
        """
        if self._shared is not None and self._shared[0].shape[1] == dim:
            return
        rows = self.store.load_all_memory_embeddings() or []
        embs = []
        meta = []
        for row in rows:
            if row["embedding"] is None:
                continue
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            if emb.shape[0] != dim:
                continue  # 维度不匹配（embedding 模型切换等），跳过
            embs.append(emb)
            meta.append(row)
        if embs:
            mat = np.vstack(embs)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            mat = mat / norms
            self._shared = (mat, meta)
        else:
            self._shared = (np.empty((0, dim), dtype=np.float32), [])
        SemanticMemory._SHARED_CACHE[self._cache_key] = self._shared

    def invalidate_cache(self):
        """使向量缓存失效（新记忆写入后调用）

        清空本实例引用并失效模块级共享缓存，下次 search 时重新加载。
        """
        self._shared = None
        SemanticMemory._SHARED_CACHE[self._cache_key] = None

    # ── 格式化输出 ──

    def format_for_prompt(self, query: str, top_k: int = 4) -> str:
        """根据用户输入生成语义检索结果，供 system prompt 注入

        Args:
            query: 用户当前的输入
            top_k: 返回条数
        """
        results = self.search(query, top_k=top_k)
        if not results:
            return ""

        lines = ["\n## 语义记忆（与当前问题相关的历史经验）\n"]
        for r in results:
            content = r["content"][:300]
            mtype = r["memory_type"]
            score = r["score"]
            lines.append(f"- [{mtype}] (相关度 {score:.2f}) {content}")

        return "\n".join(lines) + "\n"


def create_semantic_memory() -> SemanticMemory:
    return SemanticMemory()
