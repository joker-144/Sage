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
import sys
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
    # 论文元数据（索引时提取，供检索结果标注来源）
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[str] = None
    doi: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None


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
    _download_lock = None  # 类级下载锁（避免并发下载）

    def __init__(self):
        import threading
        if LocalEmbedder._download_lock is None:
            LocalEmbedder._download_lock = threading.Lock()
        # 优先从 .env 读取 HF_ENDPOINT，未设置时默认走国内镜像（避免连接超时）
        if not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
        # 设置下载超时（默认 30 秒），防止打包后首次启动在无网络环境无限等待
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

    def _ensure_model(self, progress_callback=None):
        """延迟加载模型（类级单例，所有实例共享）

        Args:
            progress_callback: 可选的回调函数，签名为 fn(stage: str, percent: int, message: str)
                stage: "checking" | "downloading" | "loading" | "ready" | "error"
                percent: 0-100 的整数进度
                message: 当前阶段描述

        若模型下载失败（超时/网络不可达），给出明确提示而非静默阻塞。
        """
        if LocalEmbedder._model is not None:
            if progress_callback:
                progress_callback("ready", 100, "模型已就绪")
            return LocalEmbedder._model

        with LocalEmbedder._download_lock:
            # 双重检查（锁内再次确认）
            if LocalEmbedder._model is not None:
                if progress_callback:
                    progress_callback("ready", 100, "模型已就绪")
                return LocalEmbedder._model

            from sage.config import get_config
            config = get_config()
            model_name = config.llm_embedding_model

            if progress_callback:
                progress_callback("checking", 0, "正在检查模型缓存...")

            # 已缓存 → 直接加载
            if self._is_model_cached(model_name):
                if progress_callback:
                    progress_callback("loading", 50, "正在加载模型到内存...")
                try:
                    from sentence_transformers import SentenceTransformer
                    LocalEmbedder._model = SentenceTransformer(model_name)
                    if progress_callback:
                        progress_callback("ready", 100, "模型就绪")
                    return LocalEmbedder._model
                except Exception as e:
                    if progress_callback:
                        progress_callback("error", 0, f"加载失败: {e}")
                    raise

            # 需要下载 → 流式下载并报告进度
            try:
                from sentence_transformers import SentenceTransformer
                model_dir = self._download_model_streaming(model_name, progress_callback)

                if progress_callback:
                    progress_callback("loading", 95, "正在加载模型到内存...")

                LocalEmbedder._model = SentenceTransformer(model_dir or model_name)

                if progress_callback:
                    progress_callback("ready", 100, "模型就绪")

            except Exception as e:
                if progress_callback:
                    progress_callback("error", 0, f"下载失败: {e}")
                # 给出友好提示
                error_msg = (
                    f"Embedding 模型加载失败: {e}\n"
                    f"模型 '{model_name}' 无法下载。可能原因:\n"
                    "  1. 当前网络环境无法访问 HuggingFace（可尝试设置代理）\n"
                    "  2. 防火墙阻止了 HTTPS 连接\n"
                    "  3. 磁盘空间不足\n"
                    "语义搜索和记忆检索功能将降级不可用，但对话功能不受影响。"
                )
                print(f"[Sage] {error_msg}", file=sys.stderr)
                raise RuntimeError(error_msg) from e

        return LocalEmbedder._model

    def _download_model_streaming(self, model_name: str, progress_callback=None) -> str:
        """使用 httpx 流式下载模型文件，通过 progress_callback 报告进度。

        下载策略：
        1. 使用 huggingface_hub API 获取文件列表
        2. 仅下载必需文件（.json, .safetensors, .txt 等），跳过 .bin/.h5/.msgpack 等
        3. 对 >1MB 的文件启用流式下载 + 进度回调
        4. 小文件直接走 hf_hub_download（有缓存/断点续传支持）

        Returns:
            模型缓存目录的路径
        """
        import shutil
        import tempfile
        from huggingface_hub import hf_hub_download, list_repo_files

        if progress_callback:
            progress_callback("downloading", 0, "正在获取文件列表...")

        # 获取仓库文件列表
        try:
            all_files = list_repo_files(model_name)
        except Exception:
            # API 不可达时回退到直接让 SentenceTransformer 下载
            return None

        # 筛选需要下载的文件（排除不必要的大文件）
        download_files: list[str] = []
        skip_extensions = {".bin", ".h5", ".msgpack", ".pt", ".pth", ".ckpt", ".onnx"}
        for f in all_files:
            ext = Path(f).suffix.lower()
            if ext in skip_extensions:
                continue
            download_files.append(f)

        if not download_files:
            return None

        # 估算总大小（通过 Content-Length）
        total_bytes = 0
        file_sizes: dict[str, int] = {}
        from huggingface_hub import hf_hub_url

        try:
            import httpx
            hf_endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
            base_url = hf_endpoint.rstrip("/")

            for fname in download_files:
                try:
                    url = f"{base_url}/{model_name}/resolve/main/{fname}"
                    with httpx.Client(timeout=10, follow_redirects=True) as client:
                        head_resp = client.head(url)
                        if head_resp.status_code == 200:
                            size = int(head_resp.headers.get("content-length", 0))
                            file_sizes[fname] = size
                            total_bytes += size
                except Exception:
                    pass
        except Exception:
            pass

        if total_bytes == 0:
            total_bytes = 90 * 1024 * 1024  # all-MiniLM-L6-v2 约 90MB

        total_mb = total_bytes / (1024 * 1024)
        if progress_callback:
            progress_callback("downloading", 0, f"开始下载 ({total_mb:.0f}MB)...")

        # 逐文件下载
        downloaded_bytes = 0

        for fname in download_files:
            fsize = file_sizes.get(fname, 0)
            is_large = fsize > 1 * 1024 * 1024  # >1MB 用流式下载

            if is_large:
                # 流式下载大文件（如 model.safetensors）
                try:
                    import httpx
                    url = f"{base_url}/{model_name}/resolve/main/{fname}"
                    # 获取缓存目录
                    from huggingface_hub.constants import HF_HUB_CACHE
                    cache_dir = Path(HF_HUB_CACHE) if HF_HUB_CACHE else Path.home() / ".cache" / "huggingface" / "hub"
                    repo_cache = cache_dir / f"models--{model_name.replace('/', '--')}" / "snapshots"
                    # 先确保目录存在
                    repo_cache.mkdir(parents=True, exist_ok=True)

                    # 下载到临时文件
                    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(fname).suffix) as tmp:
                        tmp_path = tmp.name

                    try:
                        with httpx.Client(timeout=120, follow_redirects=True) as client:
                            with client.stream("GET", url) as resp:
                                resp.raise_for_status()
                                with open(tmp_path, "wb") as f:
                                    chunk_idx = 0
                                    for chunk in resp.iter_bytes(1024 * 1024):
                                        f.write(chunk)
                                        chunk_idx += 1
                                        downloaded_bytes += len(chunk)
                                        if progress_callback and total_bytes > 0:
                                            pct = min(int(downloaded_bytes * 100 / total_bytes), 95)
                                            dl_mb = downloaded_bytes / (1024 * 1024)
                                            progress_callback(
                                                "downloading", pct,
                                                f"下载中 {dl_mb:.1f}/{total_mb:.1f}MB ({pct}%)"
                                            )

                        # 移动到缓存目录
                        dest = repo_cache / fname
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(tmp_path, str(dest))
                    except Exception:
                        # 清理临时文件
                        try:
                            Path(tmp_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise

                except Exception as e:
                    # 大文件流式下载失败 → 回退到 hf_hub_download
                    if progress_callback:
                        progress_callback("downloading", min(int(downloaded_bytes * 100 / total_bytes), 95),
                                          f"切换下载方式: {Path(fname).name}")
                    try:
                        hf_hub_download(model_name, fname, resume_download=True)
                        # 更新已下载字节（无法精确获取，用文件大小估算）
                        if fsize > 0:
                            downloaded_bytes += fsize
                    except Exception:
                        # 跳过此文件，让 SentenceTransformer 自己处理
                        pass
            else:
                # 小文件直接用 hf_hub_download（有缓存支持）
                try:
                    hf_hub_download(model_name, fname, resume_download=True)
                except Exception:
                    pass
                if fsize > 0:
                    downloaded_bytes += fsize

        if progress_callback:
            progress_callback("downloading", 95, "下载完成")

        return None  # 让 SentenceTransformer 从缓存加载

    @staticmethod
    def _is_model_cached(model_name: str) -> bool:
        """检查模型是否已在 HuggingFace 缓存中"""
        try:
            from huggingface_hub import try_to_load_from_cache
            return try_to_load_from_cache(model_name, "config.json") is not None
        except Exception:
            # 无法检测缓存状态时假定未缓存（保守策略）
            return False

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


# ── Cross-Encoder 重排器（二阶段精排）──

class CrossEncoderReranker:
    """Cross-Encoder 重排器 — 对召回结果精排

    特点:
      - 基于 cross-encoder/ms-marco-MiniLM-L-6-v2（约 80MB）
      - 接受 (query, candidate) 对，输出相关性分数
      - 首次使用通过 hf-mirror.com 国内镜像下载，无需 VPN
      - 模型加载失败时优雅降级（跳过重排，返回原顺序）
    """

    _model = None  # 类级单例
    _load_failed = False  # 加载失败标记（避免反复尝试）

    def __init__(self):
        # 优先从 .env 读取 HF_ENDPOINT，未设置时默认走国内镜像
        if not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

    def _ensure_model(self):
        """延迟加载模型（类级单例）"""
        if CrossEncoderReranker._model is not None:
            return True
        if CrossEncoderReranker._load_failed:
            return False
        try:
            from sentence_transformers import CrossEncoder
            CrossEncoderReranker._model = CrossEncoder(
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                max_length=512,
            )
            return True
        except Exception:
            # 模型下载/加载失败 — 标记降级，后续调用跳过重排
            CrossEncoderReranker._load_failed = True
            return False

    def rerank(self, query: str, chunks: list[CodeChunk], top_k: int = 5) -> list[CodeChunk]:
        """对召回结果重排

        Args:
            query: 用户查询
            chunks: 召回的文档块（bi-encoder 阶段结果）
            top_k: 重排后返回的数量

        Returns:
            重排后的 top_k 文档块（score 字段更新为 cross-encoder 分数）
        """
        if not chunks:
            return []
        if not self._ensure_model():
            # 降级：返回原顺序的 top_k
            return chunks[:top_k]
        try:
            pairs = [(query, c.content[:1500]) for c in chunks]
            scores = CrossEncoderReranker._model.predict(pairs, batch_size=32)
            for chunk, score in zip(chunks, scores):
                chunk.score = float(score)
            chunks.sort(key=lambda c: c.score, reverse=True)
            return chunks[:top_k]
        except Exception:
            # 重排出错 — 降级返回原顺序
            return chunks[:top_k]


# ── 索引级别配置 ──
# 两种索引级别：标准索引 / 高精度索引
# 区别在于分块粒度、召回 Top-K、是否启用 cross-encoder 重排
# 向量维度相同（都用 all-MiniLM-L6-v2，384 维），不涉及模型切换
INDEX_LEVEL_STANDARD = "standard"  # 标准索引
INDEX_LEVEL_PREMIUM = "premium"    # 高精度索引

# 各级别参数表
_INDEX_LEVEL_PARAMS = {
    INDEX_LEVEL_STANDARD: {
        "max_chunk_lines": 80,   # 分块行数上限（~800 字符）
        "default_top_k": 5,      # 召回 Top-K
        "default_rerank": False, # 不启用 cross-encoder 重排（速度快）
    },
    INDEX_LEVEL_PREMIUM: {
        "max_chunk_lines": 40,   # 分块行数上限（~400 字符，更细粒度）
        "default_top_k": 10,     # 召回 Top-K 更大
        "default_rerank": True,  # 启用 cross-encoder 重排（精度高）
    },
}

# 旧版本索引级别（SCI/SSCI/CSSCI/EI）归一化映射，确保已有工作空间兼容
_LEGACY_LEVEL_MAP = {
    "SCI": INDEX_LEVEL_STANDARD,
    "SSCI": INDEX_LEVEL_STANDARD,
    "CSSCI": INDEX_LEVEL_STANDARD,
    "EI": INDEX_LEVEL_STANDARD,
}


def _normalize_index_level(level: str | None) -> str:
    """归一化索引级别，兼容旧版本值。

    旧版本使用 SCI/SSCI/CSSCI/EI（功能相同），统一归一化为 standard。
    新版本使用 standard / premium。
    未知值默认归一化为 standard（保守降级）。
    """
    if not level:
        return INDEX_LEVEL_STANDARD
    level = str(level).strip().lower()
    if level == INDEX_LEVEL_PREMIUM:
        return INDEX_LEVEL_PREMIUM
    # 旧值或 standard 都归一化为 standard
    return INDEX_LEVEL_STANDARD


# ── 项目索引 ──

class ProjectIndex:
    """论文文档索引 — 文件分块 + Embedding + 向量检索

    Sage 系统中用于索引工作空间内的论文文档，支持文本格式和二进制格式。

    索引级别（从工作空间 .sage/meta.json 读取）:
      - standard: 标准索引（分块 80 行 / 召回 5 / 不重排）
      - premium:  高精度索引（分块 40 行 / 召回 10 / cross-encoder 重排）
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
        ".sage",   # 排除 Sage 工作空间配置目录（索引数据库等）
    }

    # 单文件最大块数（防止超大文件拖慢索引）
    # 代码文件用固定值；论文文档（PDF/DOCX/MD 等）通过 _max_chunks_for_file 动态放宽
    MAX_CHUNKS_PER_FILE = 50
    # 论文文档最大块数（确保文末参考文献等尾部内容不被截断丢弃）
    MAX_CHUNKS_PER_DOC = 300

    # 索引 schema 版本：分块策略变更时递增此值，触发自动强制重建索引
    # v2: 论文文档 MAX_CHUNKS 从 50 提升到 300，修复文末参考文献被截断问题
    INDEX_SCHEMA_VERSION = 2

    def __init__(self, workspace: Path, store: Optional[MemoryStore] = None):
        self.workspace = workspace.resolve()
        self.store = store or MemoryStore()
        self._embedder = None  # 延迟初始化（首次使用时创建）
        self._reranker = None  # Cross-Encoder 重排器（延迟初始化）
        self._embeddings_cache: list[dict] | None = None  # 向量缓存（索引后失效）
        # 从工作空间元数据读取索引级别（standard / premium）
        self.index_level = self._read_index_level()

    def _read_index_level(self) -> str:
        """从工作空间 .sage/meta.json 读取索引级别

        读取失败或文件不存在时默认返回 standard。
        旧版本值（SCI/SSCI/CSSCI/EI）自动归一化为 standard。
        """
        try:
            import json as _json
            meta_path = self.workspace / ".sage" / "meta.json"
            if meta_path.exists():
                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                return _normalize_index_level(meta.get("index_level"))
        except Exception:
            pass
        return INDEX_LEVEL_STANDARD

    @property
    def max_chunk_lines(self) -> int:
        """当前索引级别的分块行数上限（动态，根据 index_level）"""
        return _INDEX_LEVEL_PARAMS[self.index_level]["max_chunk_lines"]

    def _max_chunks_for_file(self, file_path: str) -> int:
        """根据文件类型返回最大块数

        论文文档（PDF/DOCX/RTF/MD/TXT/TEX/BIB 等）放宽到 MAX_CHUNKS_PER_DOC，
        确保文末参考文献等尾部内容不被截断；
        代码文件保持 MAX_CHUNKS_PER_FILE 防止超大文件拖慢索引。
        """
        ext = Path(file_path).suffix.lower()
        if ext in self.BINARY_EXTENSIONS or ext in {
            ".md", ".txt", ".rst", ".tex", ".bib", ".csv", ".tsv",
        }:
            return self.MAX_CHUNKS_PER_DOC
        return self.MAX_CHUNKS_PER_FILE

    @property
    def embedder(self) -> LocalEmbedder:
        """延迟创建本地 Embedder"""
        if self._embedder is None:
            self._embedder = LocalEmbedder()
        return self._embedder

    @property
    def reranker(self) -> CrossEncoderReranker:
        """延迟创建 Cross-Encoder 重排器"""
        if self._reranker is None:
            self._reranker = CrossEncoderReranker()
        return self._reranker

    # ── 索引 ──

    def _check_index_version(self) -> bool:
        """检查索引 schema 版本是否匹配，返回是否需要强制重建"""
        version_file = self.workspace / ".sage" / "index_version"
        try:
            if version_file.exists():
                stored = int(version_file.read_text(encoding="utf-8").strip())
                return stored < self.INDEX_SCHEMA_VERSION
        except Exception:
            pass
        # 文件不存在或读取失败 → 需要重建
        return True

    def _write_index_version(self):
        """写入当前索引 schema 版本"""
        try:
            version_file = self.workspace / ".sage" / "index_version"
            version_file.parent.mkdir(parents=True, exist_ok=True)
            version_file.write_text(str(self.INDEX_SCHEMA_VERSION), encoding="utf-8")
        except Exception:
            pass

    def index_project(self, force: bool = False) -> dict:
        """索引整个工作空间（论文文档 + 文本文件）

        Args:
            force: 是否强制重新索引（忽略 hash 跳过逻辑）

        Returns:
            统计信息 {"files": N, "chunks": N, "skipped": N}
        """
        stats = {"files": 0, "chunks": 0, "skipped": 0}

        # 索引 schema 版本不匹配时自动强制重建（如分块策略变更）
        if not force and self._check_index_version():
            force = True

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

            try:
                # 清理旧索引，重新分块
                self.store.delete_file_chunks(rel_path)

                chunks = self._chunk_file(content, rel_path)
                if not chunks:
                    continue

                # 提取论文元数据（标题/作者/年份/DOI），用于检索结果标注来源
                metadata = self._extract_paper_metadata(content, file_path)

                # PDF 文件：为每个 chunk 补充页码信息
                if file_path.suffix.lower() == ".pdf":
                    page_map = self._build_pdf_page_map(file_path)
                    for chunk in chunks:
                        ps, pe = self._lines_to_pages(chunk.start_line, chunk.end_line, page_map)
                        chunk.page_start = ps
                        chunk.page_end = pe

                # 将元数据附加到每个 chunk
                for chunk in chunks:
                    chunk.title = metadata.get("title")
                    chunk.authors = metadata.get("authors")
                    chunk.year = metadata.get("year")
                    chunk.doi = metadata.get("doi")

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
                        title=chunk.title,
                        authors=chunk.authors,
                        year=chunk.year,
                        doi=chunk.doi,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                    )
                    stats["chunks"] += 1

                stats["files"] += 1
            except Exception as e:
                # 单个文件处理失败不影响其他文件
                print(f"[Sage] 索引文件失败 {rel_path}: {e}", file=sys.stderr)
                continue

        # 索引完成后写入 schema 版本号
        if stats["files"] > 0 or force:
            self._write_index_version()

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
        """使用 PyMuPDF (fitz) 提取 PDF 文本

        对于有文本层的原生 PDF 直接提取；
        对于文本层为空的扫描版 PDF，自动调用 RapidOCR 识别图片文字。
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            try:
                import pymupdf as fitz
            except ImportError:
                return ""
        text_parts = []
        doc = fitz.open(str(file_path))
        try:
            # 延迟初始化 OCR 引擎（仅扫描版 PDF 需要时才加载）
            ocr_engine = None
            for page in doc:
                page_text = page.get_text()
                if page_text.strip():
                    # 有文本层 — 直接使用
                    text_parts.append(page_text)
                else:
                    # 文本层为空 — 可能是扫描版，调用 OCR
                    if ocr_engine is None:
                        ocr_engine = self._get_ocr_engine()
                    if ocr_engine is not None:
                        ocr_text = self._ocr_page(page, ocr_engine)
                        text_parts.append(ocr_text if ocr_text else f"[页面 {page.number + 1} OCR 无识别结果]")
                    else:
                        text_parts.append(f"[页面 {page.number + 1} 为扫描版，OCR 引擎未安装]")
        finally:
            doc.close()
        return "\n\n".join(text_parts)

    def _get_ocr_engine(self):
        """延迟加载 RapidOCR 引擎（类级单例）

        Returns:
            RapidOCR 实例，加载失败返回 None
        """
        if hasattr(self, "_ocr_engine_cached") and self._ocr_engine_cached is not None:
            return self._ocr_engine_cached
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_engine_cached = RapidOCR()
            return self._ocr_engine_cached
        except Exception:
            self._ocr_engine_cached = None
            return None

    def _ocr_page(self, page, ocr_engine) -> str:
        """对单页 PDF 执行 OCR

        Args:
            page: fitz.Page 对象
            ocr_engine: RapidOCR 实例

        Returns:
            识别出的文本（多行拼接），失败返回空字符串
        """
        try:
            # 将页面渲染为图片（DPI=200 兼顾清晰度与速度）
            pix = page.get_pixmap(dpi=200)
            import numpy as np
            img_bytes = pix.tobytes("png")
            # RapidOCR 接受图片字节或 numpy 数组
            result, _ = ocr_engine(img_bytes)
            if not result:
                return ""
            # result 是 list of [box, text, score]
            lines = [item[1] for item in result if item and len(item) >= 2]
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_pdf_page_map(self, file_path: Path) -> list[tuple[int, int]]:
        """构建 PDF 行号→页号映射表

        Returns:
            list of (start_line, page_number)，按 start_line 升序
            每页的文本用两个换行连接，页内首行即为该页起始行号（1-based）
        """
        try:
            import fitz
        except ImportError:
            try:
                import pymupdf as fitz
            except ImportError:
                return []
        page_map: list[tuple[int, int]] = []
        line_offset = 1  # 全局行号从 1 开始
        try:
            doc = fitz.open(str(file_path))
            for page_idx, page in enumerate(doc, start=1):
                page_map.append((line_offset, page_idx))
                # 每页文本的行数 + 页间分隔的 2 个空行（与 _extract_pdf_text 一致）
                page_text = page.get_text()
                line_count = page_text.count("\n") + 1 + 2  # +2 为页间 "\n\n" 分隔
                line_offset += line_count
            doc.close()
        except Exception:
            return []
        return page_map

    def _lines_to_pages(
        self, start_line: int, end_line: int, page_map: list[tuple[int, int]]
    ) -> tuple[Optional[int], Optional[int]]:
        """根据行号范围反查 PDF 页号范围

        Args:
            start_line: chunk 起始行（1-based）
            end_line: chunk 结束行
            page_map: _build_pdf_page_map 的返回值

        Returns:
            (page_start, page_end)，查不到返回 (None, None)
        """
        if not page_map:
            return None, None
        page_start = None
        page_end = None
        for line_offset, page_num in page_map:
            if line_offset <= start_line:
                page_start = page_num
            if line_offset <= end_line:
                page_end = page_num
            else:
                break
        return page_start, page_end

    def _extract_paper_metadata(self, content: str, file_path: Path) -> dict:
        """从论文内容提取元数据（标题/作者/年份/DOI）

        优先调用 paper_ops.PaperOps._extract_paper_metadata 的正则规则，失败时用简单正则兜底。
        """
        metadata: dict = {"title": None, "authors": None, "year": None, "doi": None}
        try:
            # 调用 tools/paper_ops.py 中的同步私有方法（接收 content 字符串）
            from sage.tools.paper_ops import PaperOps
            ops = PaperOps(self.workspace)
            extracted = ops._extract_paper_metadata(content)
            metadata["title"] = extracted.get("title")
            metadata["authors"] = extracted.get("authors")
            metadata["year"] = extracted.get("year")
            metadata["doi"] = extracted.get("doi")
        except Exception:
            pass

        # 兜底：用简单正则补充缺失字段
        if not metadata["doi"]:
            m = re.search(r"\b10\.\d{4,9}/[^\s\"<>]+\b", content)
            if m:
                metadata["doi"] = m.group(0).rstrip(".,;)")
        if not metadata["year"]:
            m = re.search(r"\b(19|20)\d{2}\b", content[:2000])
            if m:
                metadata["year"] = m.group(0)
        return metadata

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
        # 论文文档扩展名（放宽大小限制）
        doc_extensions = self.BINARY_EXTENSIONS | {".md", ".txt", ".rst", ".tex", ".bib", ".csv", ".tsv"}
        # 代码/配置文件大小限制 5MB；论文文档大小限制 100MB
        max_size_code = 5 * 1024 * 1024
        max_size_doc = 100 * 1024 * 1024

        for root, dirs, files in os.walk(self.workspace):
            # 过滤排除目录（原地修改 dirs 实现 prune）
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext not in all_extensions:
                    continue
                # 根据文件类型应用不同的大小限制
                try:
                    fsize = fpath.stat().st_size
                    if ext in doc_extensions:
                        if fsize > max_size_doc:
                            continue
                    else:
                        if fsize > max_size_code:
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
        max_chunks = self._max_chunks_for_file(file_path)

        chunks: list[CodeChunk] = []
        current_start = 0
        chunk_count = 0

        for i, line in enumerate(lines):
            # 达到行数上限或遇到新的语义边界（非首行）
            is_boundary = i > 0 and boundary_re.match(line)
            is_full = (i - current_start) >= self.max_chunk_lines

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
                if chunk_count >= max_chunks:
                    break

        # 收尾：最后一块
        if current_start < len(lines) and chunk_count < max_chunks:
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
          3. 兼顾 max_chunk_lines 上限（按索引级别动态调整）
        """
        # 标题边界正则（Markdown # / LaTeX \section / 数字编号）
        heading_re = re.compile(
            r"^\s*(#{1,6}\s|\\section\{|\\subsection\{|\\subsubsection\{|"
            r"\d+\.?\d*\.?\d*\s+\S)",
            re.MULTILINE,
        )
        max_chunks = self._max_chunks_for_file(file_path)

        chunks: list[CodeChunk] = []
        current_start = 0
        chunk_count = 0
        current_lines = 0  # 当前块累计行数

        for i, line in enumerate(lines):
            is_heading = i > 0 and heading_re.match(line)
            is_blank = i > 0 and not line.strip() and current_lines > 10
            is_full = current_lines >= self.max_chunk_lines

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
                if chunk_count >= max_chunks:
                    break

            current_lines += 1

        # 收尾：最后一块
        if current_start < len(lines) and chunk_count < max_chunks:
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

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        threshold: float = 0.3,
        rerank: Optional[bool] = None,
    ) -> list[CodeChunk]:
        """语义搜索代码库

        二阶段检索：bi-encoder 召回 + cross-encoder 精排。
        余弦相似度计算与维度无关，自动适配本地 Embedder 的向量维度。

        Args:
            query: 自然语言查询
            top_k: 最终返回结果数量。None 时按索引级别自动选择
                   （standard=5, premium=10）；显式传值时尊重调用方
            threshold: bi-encoder 相似度阈值，低于此值的结果不进入重排（默认 0.3）
            rerank: 是否启用 cross-encoder 重排。None 时按索引级别自动选择
                    （standard=False, premium=True）；显式传值时尊重调用方

        Returns:
            最相关的代码块列表（按相似度降序，含论文元数据）
        """
        # 根据索引级别填充默认值（显式传值时尊重调用方）
        if top_k is None:
            top_k = _INDEX_LEVEL_PARAMS[self.index_level]["default_top_k"]
        if rerank is None:
            rerank = _INDEX_LEVEL_PARAMS[self.index_level]["default_rerank"]

        # 1. 将 query 转为 embedding
        query_vec = self.embedder.encode([query])[0]

        # 2. 加载所有向量做余弦相似度搜索（带缓存）
        if self._embeddings_cache is None:
            self._embeddings_cache = self.store.load_all_embeddings()
        rows = self._embeddings_cache
        if not rows:
            return []

        # 计算相似度（维度自动适配）+ 阈值过滤
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
            # 阈值过滤：低于阈值的不召回
            if score < threshold:
                continue
            scored.append(CodeChunk(
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content=row["content"],
                score=score,
                title=row.get("title"),
                authors=row.get("authors"),
                year=row.get("year"),
                doi=row.get("doi"),
                page_start=row.get("page_start"),
                page_end=row.get("page_end"),
            ))

        if not scored:
            return []

        # 3. 按相似度降序取候选集（top_k * 4，为重排提供更大候选池）
        scored.sort(key=lambda c: c.score, reverse=True)
        candidates = scored[: max(top_k * 4, top_k)]

        # 4. 二阶段：cross-encoder 重排
        if rerank and len(candidates) > 1:
            candidates = self.reranker.rerank(query, candidates, top_k=top_k)
        else:
            candidates = candidates[:top_k]

        return candidates
