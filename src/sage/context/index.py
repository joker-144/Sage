"""
论文文档索引 — 文件分块 + Embedding + 向量检索

Sage 论文写作系统的核心组件。它让 Agent 能"看到"工作空间中所有论文文档的内容，
并根据写作需求检索相关文献片段——这是 Sage "理解你的文献库"的核心机制。

工作流程:
  1. index_project(): 遍历工作空间文档 → 按段落/章节分块 → Embedding → 存入 SQLite
  2. search(): 将查询转为 Embedding → 在 SQLite 中做余弦相似度搜索 → 返回相关文档块

支持的文档格式:
  - 文本格式: .md/.txt/.rst/.tex/.bib/.csv/.tsv
  - 二进制格式: .pdf/.docx/.rtf（需安装可选依赖 PyMuPDF/python-docx）

Embedding: 本地 sentence-transformers（all-MiniLM-L6-v2，384 维）
  - 模型约 80MB，首次使用时通过 huggingface-hub 官方源下载
  - 可在 .env 中设置 HF_ENDPOINT=https://hf-mirror.com 切换国内镜像
  - 纯本地推理，无网络调用开销，无 API Key 依赖
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from sage.memory.store import MemoryStore


@dataclass
class CodeChunk:
    """文档块（兼容原 CodeChunk 命名，Sage 中用于论文文档分块）"""
    file_path: str
    start_line: int
    end_line: int
    content: str
    score: float = 0.0  # 搜索时的相似度得分


# ── 本地 Embedder（sentence-transformers）──

class LocalEmbedder:
    """本地 Embedder — 基于 sentence-transformers 的 all-MiniLM-L6-v2

    特点:
      - 纯本地推理，无 API 调用，无网络开销
      - 模型约 80MB，首次使用通过 huggingface-hub 官方源下载
      - 输出 384 维向量，适合语义搜索和记忆检索
      - 可在 .env 中设置 HF_ENDPOINT=https://hf-mirror.com 切换国内镜像
    """

    _model = None  # 类级单例（避免重复加载模型）

    def __init__(self):
        # 优先从 .env 读取 HF_ENDPOINT，未设置时默认走国内镜像（避免连接超时）
        if not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

    def _ensure_model(self):
        """延迟加载模型（类级单例，所有实例共享）"""
        if LocalEmbedder._model is None:
            from sentence_transformers import SentenceTransformer
            from sage.config import get_config
            config = get_config()
            model_name = config.llm_embedding_model
            LocalEmbedder._model = SentenceTransformer(model_name)
        return LocalEmbedder._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """批量生成向量

        Args:
            texts: 待编码的文本列表

        Returns:
            np.ndarray: shape=(len(texts), 384)，dtype=float32
        """
        if not texts:
            return np.array([], dtype=np.float32)

        model = self._ensure_model()
        # sentence-transformers 的 encode 直接返回 numpy 数组
        embeddings = model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            batch_size=64,
        )
        return np.array(embeddings, dtype=np.float32)


# 兼容别名 — 旧代码中引用 ZhipuEmbedder 的地方仍可正常 import
ZhipuEmbedder = LocalEmbedder


# ── 项目索引 ──

class ProjectIndex:
    """论文文档索引 — 文件分块 + Embedding + 向量检索

    Sage 系统中用于索引工作空间内的论文文档，支持文本格式和二进制格式。
    """

    # 支持索引的文本文件扩展名（含代码文件，向后兼容）
    INDEXABLE_EXTENSIONS = {
        # 论文文档格式（文本）
        ".md", ".txt", ".rst", ".tex", ".bib", ".csv", ".tsv",
        # 代码文件（保留向后兼容）
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
        ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
        ".kt", ".scala", ".sh", ".bash", ".ps1",
        # 配置文件
        ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
        # 网页/样式
        ".html", ".css", ".scss", ".sql",
    }

    # 二进制文档扩展名（需特殊解析提取文本）
    BINARY_EXTENSIONS = {
        ".pdf", ".docx", ".rtf",
    }

    # 索引时排除的目录
    EXCLUDE_DIRS = {
        "__pycache__", ".git", ".venv", "venv", "env", "node_modules",
        ".egg-info", "data", "dist", "build", ".idea", ".vscode",
        ".agent",  # 排除 Sage 技能目录
    }

    # 单文件最大块数（防止超大文件拖慢索引）
    MAX_CHUNKS_PER_FILE = 50
    # 单块最大行数
    MAX_CHUNK_LINES = 80

    def __init__(self, workspace: Path, store: Optional[MemoryStore] = None):
        self.workspace = workspace.resolve()
        self.store = store or MemoryStore()
        self._embedder = None  # 延迟初始化（首次使用时创建）
        self._embeddings_cache: list[dict] | None = None  # 向量缓存（索引后失效）

    @property
    def embedder(self) -> LocalEmbedder:
        """延迟创建本地 Embedder"""
        if self._embedder is None:
            self._embedder = LocalEmbedder()
        return self._embedder

    # ── 索引 ──

    def index_project(self, force: bool = False) -> dict:
        """索引整个工作空间（论文文档 + 文本文件）

        Args:
            force: 是否强制重新索引（忽略 hash 跳过逻辑）

        Returns:
            统计信息 {"files": N, "chunks": N, "skipped": N}
        """
        stats = {"files": 0, "chunks": 0, "skipped": 0}

        # 索引后向量缓存失效
        self._embeddings_cache = None

        for file_path in self._walk_source_files():
            try:
                content = self._read_file_content(file_path)
            except (UnicodeDecodeError, PermissionError, OSError):
                continue

            if not content or not content.strip():
                continue

            rel_path = str(file_path.relative_to(self.workspace)).replace("\\", "/")
            file_hash = hashlib.md5(content.encode()).hexdigest()

            # 增量更新：跳过未修改的文件
            if not force and self.store.get_file_hash(rel_path) == file_hash:
                stats["skipped"] += 1
                continue

            # 清理旧索引，重新分块
            self.store.delete_file_chunks(rel_path)

            chunks = self._chunk_file(content, rel_path)
            if not chunks:
                continue

            # 批量生成 Embedding（本地 sentence-transformers）
            chunk_texts = [c.content for c in chunks]
            embeddings = self.embedder.encode(chunk_texts)

            for chunk, emb in zip(chunks, embeddings):
                emb_bytes = emb.tobytes()
                self.store.store_chunk(
                    file_path=rel_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    embedding=emb_bytes,
                    file_hash=file_hash,
                )
                stats["chunks"] += 1

            stats["files"] += 1

        return stats

    def _read_file_content(self, file_path: Path) -> str:
        """读取文件内容，自动区分文本文件和二进制文档

        对于 PDF/DOCX/RTF 等二进制格式，调用对应的解析器提取文本。
        """
        ext = file_path.suffix.lower()
        if ext in self.BINARY_EXTENSIONS:
            return self._extract_binary_text(file_path, ext)
        return file_path.read_text(encoding="utf-8")

    def _extract_binary_text(self, file_path: Path, ext: str) -> str:
        """从二进制文档中提取文本内容

        支持的格式:
          - .pdf: 使用 PyMuPDF (fitz) 提取文本
          - .docx: 使用 python-docx 提取段落文本
          - .rtf: 简单正则提取文本

        依赖未安装时返回空字符串并打印警告。
        """
        try:
            if ext == ".pdf":
                return self._extract_pdf_text(file_path)
            elif ext == ".docx":
                return self._extract_docx_text(file_path)
            elif ext == ".rtf":
                return self._extract_rtf_text(file_path)
        except Exception:
            return ""
        return ""

    def _extract_pdf_text(self, file_path: Path) -> str:
        """使用 PyMuPDF (fitz) 提取 PDF 文本"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            try:
                import pymupdf as fitz
            except ImportError:
                return ""
        text_parts = []
        doc = fitz.open(str(file_path))
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n\n".join(text_parts)

    def _extract_docx_text(self, file_path: Path) -> str:
        """使用 python-docx 提取 Word 文档文本"""
        try:
            from docx import Document
        except ImportError:
            return ""
        doc = Document(str(file_path))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())

    def _extract_rtf_text(self, file_path: Path) -> str:
        """简单 RTF 文本提取（正则去标签）"""
        try:
            raw = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        # 移除 RTF 控制字和控制符号
        clean = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", raw)
        clean = re.sub(r"[{}\\]", " ", clean)
        return clean.strip()

    def _walk_source_files(self):
        """遍历工作空间中所有可索引的文档文件（文本+二进制）"""
        all_extensions = self.INDEXABLE_EXTENSIONS | self.BINARY_EXTENSIONS
        for root, dirs, files in os.walk(self.workspace):
            # 过滤排除目录（原地修改 dirs 实现 prune）
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                if fpath.suffix.lower() in all_extensions:
                    # 跳过大文件（> 5MB，论文文档通常较大）
                    try:
                        if fpath.stat().st_size > 5 * 1024 * 1024:
                            continue
                    except OSError:
                        continue
                    yield fpath

    def _chunk_file(self, content: str, file_path: str) -> list[CodeChunk]:
        """将文档分块

        Sage 系统：根据文件类型选择分块策略
        - 代码文件（.py/.js等）：按函数/类边界切分（保留原逻辑）
        - 论文文档（.md/.txt/.pdf文本等）：按段落/标题切分
        """
        lines = content.splitlines()
        if not lines:
            return []

        # 判断文件类型选择分块策略
        ext = Path(file_path).suffix.lower()
        code_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
                           ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
                           ".kt", ".scala", ".sh", ".bash", ".ps1"}

        if ext in code_extensions:
            # 代码文件：按函数/类边界切分（保留原逻辑）
            return self._chunk_code_file(content, file_path, lines)
        else:
            # 论文文档：按段落/标题切分
            return self._chunk_document(content, file_path, lines)

    def _chunk_code_file(self, content: str, file_path: str, lines: list[str]) -> list[CodeChunk]:
        """代码文件分块（按函数/类边界，原逻辑保留）"""
        # 按语义边界分块（函数/类定义处切分）
        boundary_re = re.compile(
            r"^\s*(def |class |function |export function |export default function |"
            r"public |private |protected |static |async )",
            re.MULTILINE,
        )

        chunks: list[CodeChunk] = []
        current_start = 0
        chunk_count = 0

        for i, line in enumerate(lines):
            # 达到行数上限或遇到新的语义边界（非首行）
            is_boundary = i > 0 and boundary_re.match(line)
            is_full = (i - current_start) >= self.MAX_CHUNK_LINES

            if (is_boundary or is_full) and i > current_start:
                chunk_content = "\n".join(lines[current_start:i])
                chunks.append(CodeChunk(
                    file_path=file_path,
                    start_line=current_start + 1,
                    end_line=i,
                    content=chunk_content,
                ))
                current_start = i
                chunk_count += 1
                if chunk_count >= self.MAX_CHUNKS_PER_FILE:
                    break

        # 收尾：最后一块
        if current_start < len(lines) and chunk_count < self.MAX_CHUNKS_PER_FILE:
            chunk_content = "\n".join(lines[current_start:])
            chunks.append(CodeChunk(
                file_path=file_path,
                start_line=current_start + 1,
                end_line=len(lines),
                content=chunk_content,
            ))

        return chunks

    def _chunk_document(self, content: str, file_path: str, lines: list[str]) -> list[CodeChunk]:
        """论文文档分块（按段落/标题边界切分）

        策略:
          1. 识别 Markdown/LaTeX 标题行（#, \\section, \\subsection）作为段落边界
          2. 空行作为段落分隔符
          3. 兼顾 MAX_CHUNK_LINES 上限
        """
        # 标题边界正则（Markdown # / LaTeX \section / 数字编号）
        heading_re = re.compile(
            r"^\s*(#{1,6}\s|\\section\{|\\subsection\{|\\subsubsection\{|"
            r"\d+\.?\d*\.?\d*\s+\S)",
            re.MULTILINE,
        )

        chunks: list[CodeChunk] = []
        current_start = 0
        chunk_count = 0
        current_lines = 0  # 当前块累计行数

        for i, line in enumerate(lines):
            is_heading = i > 0 and heading_re.match(line)
            is_blank = i > 0 and not line.strip() and current_lines > 10
            is_full = current_lines >= self.MAX_CHUNK_LINES

            if (is_heading or is_full) and i > current_start:
                chunk_content = "\n".join(lines[current_start:i])
                if chunk_content.strip():
                    chunks.append(CodeChunk(
                        file_path=file_path,
                        start_line=current_start + 1,
                        end_line=i,
                        content=chunk_content,
                    ))
                current_start = i
                current_lines = 0
                chunk_count += 1
                if chunk_count >= self.MAX_CHUNKS_PER_FILE:
                    break

            current_lines += 1

        # 收尾：最后一块
        if current_start < len(lines) and chunk_count < self.MAX_CHUNKS_PER_FILE:
            chunk_content = "\n".join(lines[current_start:])
            if chunk_content.strip():
                chunks.append(CodeChunk(
                    file_path=file_path,
                    start_line=current_start + 1,
                    end_line=len(lines),
                    content=chunk_content,
                ))

        return chunks

    # ── 搜索 ──

    def search(self, query: str, top_k: int = 5) -> list[CodeChunk]:
        """语义搜索代码库

        余弦相似度计算与维度无关，自动适配本地 Embedder 的向量维度。

        Args:
            query: 自然语言查询
            top_k: 返回结果数量

        Returns:
            最相关的代码块列表（按相似度降序）
        """
        # 1. 将 query 转为 embedding
        query_vec = self.embedder.encode([query])[0]

        # 2. 加载所有向量做余弦相似度搜索（带缓存）
        if self._embeddings_cache is None:
            self._embeddings_cache = self.store.load_all_embeddings()
        rows = self._embeddings_cache
        if not rows:
            return []

        # 计算相似度（维度自动适配）
        scored: list[CodeChunk] = []
        for row in rows:
            if row["embedding"] is None:
                continue
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            if emb.shape[0] != query_vec.shape[0]:
                # 维度不匹配（旧索引数据），跳过
                continue
            score = float(np.dot(query_vec, emb) / (
                np.linalg.norm(query_vec) * np.linalg.norm(emb) + 1e-8
            ))
            scored.append(CodeChunk(
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content=row["content"],
                score=score,
            ))

        # 3. 按相似度降序，返回 top_k
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]
