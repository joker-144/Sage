"""
Sage 工作空间管理器 — 多空间管理 + 时间戳命名 + 自动向量化

Sage 论文写作系统的工作空间管理核心。每个工作空间是一个独立的论文库，
按"时间戳+领域标签"命名，支持创建多个、文件夹导入、手动上传、自动向量化。

命名规则:
  {timestamp}_{domain_tag}
  示例: 20260721_143022_CS-AI
        20260721_150000_MED-Cardio
        20260721_160000_SSCI-PSY

工作空间存储结构:
  workspaces/
  ├── registry.json                  ← 所有工作空间注册表
  ├── 20260721_143022_CS-AI/
  │   ├── .sage/
  │   │   ├── meta.json              ← 工作空间元数据（创建时间/领域/描述）
  │   │   └── index_stats.json       ← 索引统计（最近一次索引结果）
  │   ├── papers/                    ← 用户上传/导入的论文
  │   └── drafts/                    ← 生成的论文草稿
  └── ...

与原有 workspace 配置的关系:
  - 不修改 sage.config.AgentConfig.workspace 字段
  - 通过 switch_to() 方法更新 cfg.workspace 指向当前 Sage 工作空间
  - 原有 /api/workspace 接口继续可用（指向当前激活的 Sage 工作空间）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from sage.config import get_config


# Sage 工作空间根目录（所有 Sage 工作空间的父目录）
# 开发模式: 项目根目录下的 workspaces/
# 打包模式: 用户数据目录下的 workspaces/（可写）
def _get_sage_root() -> Path:
    """获取 Sage 工作空间根目录"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后：使用用户数据目录（可写）
        import os
        if os.name == 'nt':  # Windows
            base = Path(os.environ.get('APPDATA', Path.home())) / 'sage'
        else:  # macOS / Linux
            base = Path.home() / '.sage'
        base.mkdir(parents=True, exist_ok=True)
        return base / 'workspaces'
    else:
        # 开发模式：项目根目录下的 workspaces/
        project_root = Path(__file__).resolve().parent.parent.parent
        return project_root / 'workspaces'


# 领域标签合法字符正则（字母/数字/连字符/下划线，长度2-32）
_DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{2,32}$")

# 支持导入的论文文件扩展名
_PAPER_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".rst",
    ".tex", ".bib", ".rtf", ".csv", ".tsv",
}

# 工作空间名称正则：YYYYMMDD_HHMMSS_DOMAIN
_WS_NAME_PATTERN = re.compile(r"^(\d{8}_\d{6})_([A-Za-z0-9_\-]+)$")


class WorkspaceNotFoundError(Exception):
    """工作空间不存在"""


class WorkspaceAlreadyExistsError(Exception):
    """工作空间已存在"""


class InvalidDomainError(Exception):
    """领域标签非法"""


class SageWorkspaceManager:
    """Sage 多工作空间管理器

    线程安全：所有写操作通过 _lock 串行化。
    自动向量化：导入文件后自动触发索引构建（异步）。
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = root or _get_sage_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.root / "registry.json"
        self._lock = threading.Lock()
        self._ensure_registry()

    # ── 注册表管理 ──

    def _ensure_registry(self):
        """确保注册表文件存在"""
        if not self._registry_path.exists():
            self._save_registry({"workspaces": [], "version": "1.0"})

    def _load_registry(self) -> dict:
        """加载注册表"""
        try:
            return json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"workspaces": [], "version": "1.0"}

    def _save_registry(self, data: dict):
        """保存注册表"""
        self._registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _update_registry_entry(self, ws_id: str, **updates):
        """更新注册表中某个工作空间的字段"""
        with self._lock:
            reg = self._load_registry()
            for ws in reg["workspaces"]:
                if ws["id"] == ws_id:
                    ws.update(updates)
                    ws["updated_at"] = datetime.now().isoformat()
                    break
            self._save_registry(reg)

    # ── 命名规则 ──

    def _generate_ws_name(self, domain_tag: str) -> str:
        """生成工作空间名称：时间戳_领域标签

        Args:
            domain_tag: 领域标签（如 CS-AI, MED-Cardio, SSCI-PSY）

        Returns:
            工作空间名称（如 20260721_143022_CS-AI）
        """
        if not _DOMAIN_PATTERN.match(domain_tag):
            raise InvalidDomainError(
                f"领域标签非法: {domain_tag}（仅允许字母/数字/连字符/下划线，长度2-32）"
            )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{timestamp}_{domain_tag}"

    # ── 工作空间 CRUD ──

    def create_workspace(
        self,
        domain_tag: str,
        description: str = "",
        index_level: str = "standard",
    ) -> dict:
        """创建新的 Sage 工作空间

        Args:
            domain_tag: 领域标签（如 CS-AI, MED-Cardio, SSCI-PSY）
            description: 工作空间描述（可选）
            index_level: 索引级别 standard(标准索引)/premium(高精度索引)
                - standard: 分块 80 行 / 召回 Top-K=5 / 不重排（速度快）
                - premium:  分块 40 行 / 召回 Top-K=10 / cross-encoder 重排（精度高）

        Returns:
            工作空间信息字典
        """
        ws_name = self._generate_ws_name(domain_tag)
        ws_path = self.root / ws_name

        with self._lock:
            if ws_path.exists():
                raise WorkspaceAlreadyExistsError(f"工作空间已存在: {ws_name}")

            # 创建目录结构
            (ws_path / "papers").mkdir(parents=True, exist_ok=True)
            (ws_path / "drafts").mkdir(parents=True, exist_ok=True)
            (ws_path / ".sage").mkdir(parents=True, exist_ok=True)

            # 写入元数据
            meta = {
                "id": ws_name,
                "name": ws_name,
                "domain_tag": domain_tag,
                "description": description,
                "index_level": index_level,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "papers_count": 0,
                "indexed": False,
                "index_stats": None,
            }
            (ws_path / ".sage" / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 注册到注册表
            reg = self._load_registry()
            reg["workspaces"].append({
                "id": ws_name,
                "domain_tag": domain_tag,
                "description": description,
                "index_level": index_level,
                "created_at": meta["created_at"],
                "updated_at": meta["updated_at"],
                "papers_count": 0,
                "indexed": False,
                "path": str(ws_path),
            })
            self._save_registry(reg)

        return meta

    def list_workspaces(self) -> list[dict]:
        """列出所有工作空间（按创建时间倒序）

        papers_count 动态计算（遍历 papers/ 目录实际文件数），
        不依赖 registry 中的计数器，避免删除/导入失败导致不同步。
        """
        reg = self._load_registry()
        workspaces = sorted(
            reg["workspaces"],
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )
        # 动态修正每个工作空间的 papers_count
        for ws in workspaces:
            ws_id = ws.get("id")
            if not ws_id:
                continue
            papers_dir = self.root / ws_id / "papers"
            if papers_dir.exists():
                count = sum(
                    1 for f in papers_dir.rglob("*")
                    if f.is_file() and f.suffix.lower() in _PAPER_EXTENSIONS
                )
            else:
                count = 0
            ws["papers_count"] = count
        return workspaces

    def get_workspace(self, ws_id: str) -> dict:
        """获取工作空间详情（含元数据）"""
        ws_path = self.root / ws_id
        if not ws_path.exists():
            raise WorkspaceNotFoundError(f"工作空间不存在: {ws_id}")

        meta_path = ws_path / ".sage" / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {"id": ws_id, "name": ws_id}

        # 补充实时统计
        papers_dir = ws_path / "papers"
        papers_count = sum(
            1 for f in papers_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _PAPER_EXTENSIONS
        ) if papers_dir.exists() else 0
        meta["papers_count"] = papers_count
        meta["path"] = str(ws_path)
        return meta

    def delete_workspace(self, ws_id: str) -> dict:
        """删除工作空间（含所有文件）"""
        ws_path = self.root / ws_id
        if not ws_path.exists():
            raise WorkspaceNotFoundError(f"工作空间不存在: {ws_id}")

        with self._lock:
            shutil.rmtree(ws_path)
            reg = self._load_registry()
            reg["workspaces"] = [w for w in reg["workspaces"] if w["id"] != ws_id]
            self._save_registry(reg)

        return {"id": ws_id, "deleted": True}

    def get_workspace_path(self, ws_id: str) -> Path:
        """获取工作空间路径"""
        ws_path = self.root / ws_id
        if not ws_path.exists():
            raise WorkspaceNotFoundError(f"工作空间不存在: {ws_id}")
        return ws_path

    # ── 论文导入 ──

    def import_folder(self, ws_id: str, source_path: str) -> dict:
        """从本地文件夹导入论文到工作空间

        将源文件夹中所有支持的论文文件复制到工作空间的 papers/ 目录。
        导入完成后自动触发向量化索引。

        Args:
            ws_id: 工作空间 ID
            source_path: 源文件夹路径

        Returns:
            导入统计
        """
        ws_path = self.get_workspace_path(ws_id)
        src = Path(source_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"源路径不存在: {src}")
        if not src.is_dir():
            raise NotADirectoryError(f"源路径不是目录: {src}")

        papers_dir = ws_path / "papers"
        papers_dir.mkdir(parents=True, exist_ok=True)

        imported = []
        skipped = []
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _PAPER_EXTENSIONS:
                skipped.append(str(f))
                continue
            # 保留相对路径结构（避免重名冲突）
            rel = f.relative_to(src)
            dest = papers_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            imported.append(str(rel))

        # 更新元数据
        self._update_registry_entry(ws_id, papers_count=len(imported))
        self._update_meta(ws_id, papers_count=len(imported))

        # 不再同步触发索引 — 由前端调用 /async-index 接口异步建立索引
        index_result = None

        return {
            "workspace_id": ws_id,
            "imported_count": len(imported),
            "imported_files": imported,
            "skipped_count": len(skipped),
            "index_result": index_result,
        }

    def upload_file(
        self,
        ws_id: str,
        filename: str,
        content: bytes,
        subdir: str = "",
    ) -> dict:
        """上传单个论文文件到工作空间

        Args:
            ws_id: 工作空间 ID
            filename: 文件名（含扩展名）
            content: 文件内容（字节）
            subdir: 子目录（可选，如 "papers" 或 "drafts"）

        Returns:
            上传结果
        """
        ws_path = self.get_workspace_path(ws_id)
        target_dir = ws_path / (subdir or "papers")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        # 防止路径穿越
        if not target_path.resolve().is_relative_to(target_dir.resolve()):
            raise ValueError(f"非法文件名: {filename}")

        target_path.write_bytes(content)

        # 更新元数据
        if target_dir.name == "papers":
            meta = self.get_workspace(ws_id)
            new_count = meta.get("papers_count", 0) + 1
            self._update_registry_entry(ws_id, papers_count=new_count)
            self._update_meta(ws_id, papers_count=new_count)

            # 不再同步触发索引 — 由前端调用 /async-index 接口异步建立索引
            # 避免大文件或首次加载 embedding 模型时阻塞上传请求导致 Failed to fetch
        index_result = None

        return {
            "workspace_id": ws_id,
            "filename": filename,
            "path": str(target_path.relative_to(ws_path)),
            "size": len(content),
            "index_result": index_result,
        }

    def delete_paper(self, ws_id: str, paper_path: str) -> dict:
        """删除工作空间中的论文文件，并同步清理其索引块

        Args:
            ws_id: 工作空间 ID
            paper_path: 论文相对于工作空间根目录的路径（与 /papers 返回的 path 字段一致）

        Returns:
            删除结果
        """
        ws_path = self.get_workspace_path(ws_id)

        # 路径安全校验：拒绝绝对路径和 .. 穿越
        rel = Path(paper_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"非法路径: {paper_path}")

        target = (ws_path / rel).resolve()
        ws_root = ws_path.resolve()
        # 使用 is_relative_to 而非字符串 startswith，避免 'ws1' 误匹配 'ws10' 前缀
        if not target.is_relative_to(ws_root):
            raise ValueError(f"路径越界: {paper_path}")

        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {paper_path}")

        # 1. 删除文件
        target.unlink()

        # 2. 同步清理索引块（避免索引残留导致搜索命中已删除的论文）
        rel_path_str = str(rel).replace("\\", "/")
        sage_dir = ws_path / ".sage"
        db_path = sage_dir / "index.db"
        if db_path.exists():
            try:
                store = WorkspaceStore(db_path=str(db_path))
                store.delete_file_chunks(rel_path_str)
                store.close()
            except Exception:
                # 索引清理失败不影响文件删除主流程
                pass

        # 3. 更新论文计数
        meta = self.get_workspace(ws_id)
        new_count = max(0, meta.get("papers_count", 0) - 1)
        self._update_registry_entry(ws_id, papers_count=new_count)
        self._update_meta(ws_id, papers_count=new_count)

        return {
            "workspace_id": ws_id,
            "deleted_path": rel_path_str,
            "papers_count": new_count,
        }

    # ── 自动向量化索引 ──

    def trigger_indexing(self, ws_id: str, force: bool = False) -> dict:
        """触发工作空间的向量化索引

        使用 ProjectIndex 对工作空间内的论文建立向量索引。
        导入论文后自动调用此方法。

        每个工作空间使用独立的 SQLite 数据库（.sage/index.db），
        通过 WorkspaceStore 隔离，不影响全局 MemoryStore 单例。

        Args:
            ws_id: 工作空间 ID
            force: 是否强制重建索引

        Returns:
            索引统计
        """
        ws_path = self.get_workspace_path(ws_id)

        # 延迟导入避免循环依赖
        from sage.context.index import ProjectIndex

        # 为每个工作空间使用独立的 SQLite 数据库
        sage_dir = ws_path / ".sage"
        sage_dir.mkdir(parents=True, exist_ok=True)
        db_path = sage_dir / "index.db"

        # 使用独立的 WorkspaceStore（不污染全局 MemoryStore 单例）
        store = WorkspaceStore(db_path=str(db_path))
        index = ProjectIndex(workspace=ws_path, store=store)

        try:
            stats = index.index_project(force=force)
            index_failed = "error" in stats and stats.get("chunks", 0) == 0
        except Exception as e:
            stats = {"error": str(e), "files": 0, "chunks": 0, "skipped": 0}
            index_failed = True

        # 保存索引统计
        index_stats = {
            "workspace_id": ws_id,
            # 索引失败（异常或 0 块带 error）时不标记 indexed=True，
            # 避免前端误显示"已索引但 0 块"
            "indexed": not index_failed,
            "indexed_at": datetime.now().isoformat(),
            "force": force,
            "stats": stats,
            "db_path": str(db_path),
        }
        (sage_dir / "index_stats.json").write_text(
            json.dumps(index_stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 更新注册表
        self._update_registry_entry(
            ws_id,
            indexed=not index_failed,
            index_stats=index_stats,
        )
        self._update_meta(
            ws_id,
            indexed=not index_failed,
            index_stats=index_stats,
        )

        return index_stats

    def get_index_status(self, ws_id: str) -> dict:
        """获取工作空间索引状态"""
        ws_path = self.get_workspace_path(ws_id)
        stats_path = ws_path / ".sage" / "index_stats.json"
        if not stats_path.exists():
            return {
                "workspace_id": ws_id,
                "indexed": False,
                "stats": None,
                "message": "尚未建立索引",
            }
        return json.loads(stats_path.read_text(encoding="utf-8"))

    # ── 切换工作空间 ──

    def switch_to(self, ws_id: str) -> dict:
        """切换到指定工作空间（更新全局配置的 workspace 字段）

        切换后所有 Agent 操作将基于该工作空间。
        不修改原有 API 接口，仅更新 cfg.workspace 指向。

        Args:
            ws_id: 工作空间 ID

        Returns:
            切换结果
        """
        ws_path = self.get_workspace_path(ws_id)
        cfg = get_config()
        cfg.workspace = ws_path

        # 保存到 .env（持久化切换）
        self._persist_workspace_to_env(ws_path)

        return {
            "workspace_id": ws_id,
            "path": str(ws_path),
            "switched": True,
        }

    def _persist_workspace_to_env(self, ws_path: Path):
        """将工作空间路径持久化到 .env

        不修改 .env 的其他字段，仅更新 sage_WORKSPACE 行。
        """
        # 打包后 .env 在 SAGE_DATA_DIR 中（用户可写），开发时在当前目录
        env_path = Path(os.environ.get("SAGE_DATA_DIR", "")) / ".env" if os.environ.get("SAGE_DATA_DIR") else Path(".env")
        if not env_path.exists():
            # 开发模式回退：尝试项目根目录下的 .env
            env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if not env_path.exists():
            return

        lines = []
        found = False
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("sage_WORKSPACE="):
                    lines.append(f"sage_WORKSPACE={ws_path}")
                    found = True
                else:
                    lines.append(line)

        if not found:
            lines.append(f"sage_WORKSPACE={ws_path}")

        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── 内部辅助 ──

    def _update_meta(self, ws_id: str, **updates):
        """更新工作空间元数据文件"""
        ws_path = self.root / ws_id
        meta_path = ws_path / ".sage" / "meta.json"
        if not meta_path.exists():
            return
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update(updates)
        meta["updated_at"] = datetime.now().isoformat()
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── 单例管理 ──

_manager: Optional[SageWorkspaceManager] = None


def get_workspace_manager() -> SageWorkspaceManager:
    """获取全局工作空间管理器单例"""
    global _manager
    if _manager is None:
        _manager = SageWorkspaceManager()
    return _manager


# ── 全选池模式全局标志 ──
# pool_mode 为 True 时，智能体检索覆盖所有工作空间（跨空间搜索）
_pool_mode: bool = False


def get_pool_mode() -> bool:
    """获取当前池模式状态"""
    return _pool_mode


def set_pool_mode(value: bool) -> bool:
    """设置池模式状态，返回设置后的值"""
    global _pool_mode
    _pool_mode = bool(value)
    return _pool_mode


# ── 工作空间独立索引存储 ──

# WorkspaceStore 使用的 file_index 表结构（与 MemoryStore 保持一致）
_WORKSPACE_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB,
    file_hash TEXT NOT NULL,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 论文元数据（索引时提取，供检索结果标注来源）
    title TEXT,
    authors TEXT,
    year TEXT,
    doi TEXT,
    page_start INTEGER,
    page_end INTEGER
);
CREATE INDEX IF NOT EXISTS idx_file_path ON file_index(file_path);
"""


class WorkspaceStore:
    """工作空间独立索引存储

    与全局 MemoryStore 单例隔离，每个 Sage 工作空间使用独立的 SQLite 数据库。
    仅实现 ProjectIndex 需要的索引相关方法，不包含对话记忆/经验教训等功能。

    使用鸭子类型：ProjectIndex 只需要以下方法：
      - get_file_hash(file_path) -> Optional[str]
      - delete_file_chunks(file_path) -> None
      - store_chunk(...) -> None
      - load_all_embeddings() -> list[dict]
    """

    def __init__(self, db_path: str):
        import sqlite3
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_WORKSPACE_INDEX_SCHEMA)
        # 表迁移：旧版数据库可能缺少论文元数据字段，使用 ALTER TABLE 补全
        # （CREATE TABLE IF NOT EXISTS 不会更新已存在表的 schema）
        self._migrate_file_index_columns()
        self._conn.commit()

    def _migrate_file_index_columns(self) -> None:
        """检查并补全 file_index 表的论文元数据字段

        旧版 WorkspaceStore 创建的表仅有 6 个基础字段，缺少
        title/authors/year/doi/page_start/page_end，导致 ProjectIndex
        传入这些参数时抛 TypeError。这里通过 ALTER TABLE ADD COLUMN
        补齐字段，保留已有索引数据。
        """
        import sqlite3
        required_cols = {
            "indexed_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "title": "TEXT",
            "authors": "TEXT",
            "year": "TEXT",
            "doi": "TEXT",
            "page_start": "INTEGER",
            "page_end": "INTEGER",
        }
        try:
            existing = {row["name"] for row in self._conn.execute(
                "PRAGMA table_info(file_index)"
            ).fetchall()}
        except sqlite3.Error:
            return
        for col, col_type in required_cols.items():
            if col not in existing:
                try:
                    self._conn.execute(
                        f"ALTER TABLE file_index ADD COLUMN {col} {col_type}"
                    )
                except sqlite3.Error:
                    # 字段已存在或添加失败（如并发迁移）— 忽略
                    pass

    def get_file_hash(self, file_path: str) -> Optional[str]:
        """获取文件已索引的 hash"""
        row = self._conn.execute(
            "SELECT file_hash FROM file_index WHERE file_path = ? LIMIT 1",
            (file_path,),
        ).fetchone()
        return row["file_hash"] if row else None

    def delete_file_chunks(self, file_path: str) -> None:
        """删除指定文件的所有索引块"""
        self._conn.execute(
            "DELETE FROM file_index WHERE file_path = ?",
            (file_path,),
        )
        self._conn.commit()

    def store_chunk(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        content: str,
        embedding: bytes,
        file_hash: str,
        title: Optional[str] = None,
        authors: Optional[str] = None,
        year: Optional[str] = None,
        doi: Optional[str] = None,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
    ) -> None:
        """存储文档块（含向量与论文元数据）

        签名与 MemoryStore.store_chunk 保持一致，支持论文元数据
        （title/authors/year/doi/page_start/page_end）的存储，
        供检索结果标注来源。
        """
        self._conn.execute(
            """INSERT INTO file_index
               (file_path, start_line, end_line, content, embedding, file_hash,
                title, authors, year, doi, page_start, page_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_path, start_line, end_line, content, embedding, file_hash,
             title, authors, year, doi, page_start, page_end),
        )
        self._conn.commit()

    def load_all_embeddings(self) -> list[dict]:
        """加载所有向量（供向量搜索）"""
        rows = self._conn.execute(
            "SELECT id, file_path, start_line, end_line, content, embedding FROM file_index"
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_all(self) -> None:
        """清空所有索引数据（删除工作空间时调用）"""
        self._conn.execute("DELETE FROM file_index")
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


# ── 全局异步索引任务管理器 ──

import asyncio
import uuid as _uuid


class IndexTaskManager:
    """全局异步索引任务管理器

    在后台线程中执行工作空间的向量化索引，支持：
      - 异步非阻塞：上传完成后立即返回，索引在后台进行
      - 多文件并发：分块 + embedding 阶段并行（写入仍串行，避免 SQLite 锁冲突）
      - 进度推送：通过 asyncio.Queue 向 SSE 客户端实时推送进度
      - 状态查询：前端可轮询 /api/sage/workspaces/{ws_id}/index-status

    每个工作空间同一时刻只允许一个索引任务，重复触发会返回当前任务状态。
    """

    def __init__(self):
        # ws_id -> task info
        self._tasks: dict[str, dict] = {}
        # ws_id -> asyncio.Queue（SSE 订阅者）
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    def get_status(self, ws_id: str) -> dict:
        """获取工作空间索引任务状态"""
        task = self._tasks.get(ws_id)
        if not task:
            return {"workspace_id": ws_id, "status": "idle"}
        return {
            "workspace_id": ws_id,
            "status": task["status"],  # pending / running / done / error
            "progress": task.get("progress", 0),
            "total": task.get("total", 0),
            "current_file": task.get("current_file", ""),
            "message": task.get("message", ""),
            "stats": task.get("stats"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "error": task.get("error"),
        }

    async def subscribe(self, ws_id: str) -> asyncio.Queue:
        """订阅工作空间索引进度事件"""
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            if ws_id not in self._subscribers:
                self._subscribers[ws_id] = []
            self._subscribers[ws_id].append(queue)
        return queue

    async def unsubscribe(self, ws_id: str, queue: asyncio.Queue):
        """取消订阅"""
        async with self._lock:
            if ws_id in self._subscribers:
                self._subscribers[ws_id] = [
                    q for q in self._subscribers[ws_id] if q is not queue
                ]

    async def _emit(self, ws_id: str, event: dict):
        """向所有订阅者推送事件"""
        async with self._lock:
            subscribers = list(self._subscribers.get(ws_id, []))
        for q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def start_index(
        self,
        ws_id: str,
        force: bool = False,
    ) -> dict:
        """启动异步索引任务

        如果该工作空间已有任务在运行，返回当前状态（不重复启动）。
        """
        async with self._lock:
            existing = self._tasks.get(ws_id)
            if existing and existing["status"] in ("pending", "running"):
                return {
                    "workspace_id": ws_id,
                    "status": "already_running",
                    "progress": existing.get("progress", 0),
                    "total": existing.get("total", 0),
                    "current_file": existing.get("current_file", ""),
                }

            task_id = str(_uuid.uuid4())
            self._tasks[ws_id] = {
                "task_id": task_id,
                "status": "pending",
                "progress": 0,
                "total": 0,
                "current_file": "",
                "message": "准备索引...",
                "stats": None,
                "started_at": datetime.now().isoformat(),
                "finished_at": None,
                "error": None,
                "force": force,
            }

        # 在后台线程中执行索引（不阻塞事件循环）
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(self._run_index(ws_id, force, loop))
        return {"workspace_id": ws_id, "status": "started", "task_id": task_id}

    async def _run_index(self, ws_id: str, force: bool, loop: asyncio.AbstractEventLoop):
        """索引任务主逻辑（在事件循环中运行，CPU 密集部分丢到线程池）"""
        from sage.context.index import ProjectIndex

        task = self._tasks[ws_id]
        task["status"] = "running"

        try:
            mgr = get_workspace_manager()
            ws_path = mgr.get_workspace_path(ws_id)
            sage_dir = ws_path / ".sage"
            sage_dir.mkdir(parents=True, exist_ok=True)
            db_path = sage_dir / "index.db"
            store = WorkspaceStore(db_path=str(db_path))
            index = ProjectIndex(workspace=ws_path, store=store)

            # 先收集待索引文件列表（用于进度计算）
            file_list = list(index._walk_source_files())
            task["total"] = len(file_list)
            await self._emit(ws_id, {
                "type": "start",
                "total": len(file_list),
                "message": f"开始索引 {len(file_list)} 个文件",
            })

            stats = {"files": 0, "chunks": 0, "skipped": 0}
            index._embeddings_cache = None  # 清除向量缓存

            import hashlib
            from concurrent.futures import ThreadPoolExecutor

            # 用线程池并行做 CPU 密集的 embedding 计算
            # SQLite 写入仍串行（同一连接不能跨线程）
            max_workers = min(4, (os.cpu_count() or 2) - 1)
            executor = ThreadPoolExecutor(max_workers=max_workers)

            def process_one(file_path):
                """处理单个文件：读取 → 分块 → 提取元数据 → 生成 embedding"""
                try:
                    content = index._read_file_content(file_path)
                except (UnicodeDecodeError, PermissionError, OSError):
                    return None
                if not content or not content.strip():
                    return None
                rel_path = str(file_path.relative_to(index.workspace)).replace("\\", "/")
                file_hash = hashlib.md5(content.encode()).hexdigest()
                if not force and index.store.get_file_hash(rel_path) == file_hash:
                    return ("skipped", rel_path, file_hash, None, None)
                chunks = index._chunk_file(content, rel_path)
                if not chunks:
                    return None
                metadata = index._extract_paper_metadata(content, file_path)
                if file_path.suffix.lower() == ".pdf":
                    page_map = index._build_pdf_page_map(file_path)
                    for chunk in chunks:
                        ps, pe = index._lines_to_pages(chunk.start_line, chunk.end_line, page_map)
                        chunk.page_start = ps
                        chunk.page_end = pe
                for chunk in chunks:
                    chunk.title = metadata.get("title")
                    chunk.authors = metadata.get("authors")
                    chunk.year = metadata.get("year")
                    chunk.doi = metadata.get("doi")
                chunk_texts = [c.content for c in chunks]
                embeddings = index.embedder.encode(chunk_texts)
                return ("indexed", rel_path, file_hash, chunks, embeddings)

            # 提交所有文件到线程池
            futures = {executor.submit(process_one, fp): fp for fp in file_list}
            done_count = 0
            for future in futures:
                # 顺序收集结果（保证 SQLite 写入串行）
                result = await loop.run_in_executor(None, lambda f=future: f.result())
                fp = futures[future]
                done_count += 1
                task["progress"] = done_count
                task["current_file"] = str(fp.name)

                if result is None:
                    continue
                kind, rel_path, file_hash, chunks, embeddings = result
                if kind == "skipped":
                    stats["skipped"] += 1
                    await self._emit(ws_id, {
                        "type": "progress",
                        "progress": done_count,
                        "total": len(file_list),
                        "current_file": str(fp.name),
                        "message": f"跳过未修改: {fp.name}",
                    })
                    continue

                # 清理旧索引并写入（串行）
                index.store.delete_file_chunks(rel_path)
                for chunk, emb in zip(chunks, embeddings):
                    index.store.store_chunk(
                        file_path=rel_path,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                        embedding=emb.tobytes(),
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

                await self._emit(ws_id, {
                    "type": "progress",
                    "progress": done_count,
                    "total": len(file_list),
                    "current_file": str(fp.name),
                    "message": f"已索引: {fp.name}（+{len(chunks)} 块）",
                })

            executor.shutdown(wait=False)

            # 索引完成，更新元数据
            index_failed = ("error" in stats and stats.get("chunks", 0) == 0)
            index_stats = {
                "workspace_id": ws_id,
                "indexed": not index_failed,
                "indexed_at": datetime.now().isoformat(),
                "force": force,
                "stats": stats,
                "db_path": str(db_path),
            }
            (sage_dir / "index_stats.json").write_text(
                json.dumps(index_stats, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            mgr._update_registry_entry(ws_id, indexed=not index_failed, index_stats=index_stats)
            mgr._update_meta(ws_id, indexed=not index_failed, index_stats=index_stats)

            task["status"] = "done"
            task["stats"] = stats
            task["finished_at"] = datetime.now().isoformat()
            task["message"] = f"索引完成：{stats['files']} 个文件，{stats['chunks']} 个块"
            await self._emit(ws_id, {
                "type": "done",
                "stats": stats,
                "message": task["message"],
            })

        except Exception as e:
            task["status"] = "error"
            task["error"] = str(e)
            task["finished_at"] = datetime.now().isoformat()
            task["message"] = f"索引失败: {e}"
            await self._emit(ws_id, {
                "type": "error",
                "error": str(e),
                "message": task["message"],
            })


# 全局单例
_index_task_manager: Optional[IndexTaskManager] = None


def get_index_task_manager() -> IndexTaskManager:
    global _index_task_manager
    if _index_task_manager is None:
        _index_task_manager = IndexTaskManager()
    return _index_task_manager
