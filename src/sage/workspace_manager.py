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
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from sage.config import get_config


# Sage 工作空间根目录（所有 Sage 工作空间的父目录）
# 默认为项目根目录下的 workspaces/
def _get_sage_root() -> Path:
    """获取 Sage 工作空间根目录"""
    # 项目根目录：src/sage/ 的上两级
    project_root = Path(__file__).resolve().parent.parent.parent
    return project_root / "workspaces"


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
        index_level: str = "SCI",
    ) -> dict:
        """创建新的 Sage 工作空间

        Args:
            domain_tag: 领域标签（如 CS-AI, MED-Cardio, SSCI-PSY）
            description: 工作空间描述（可选）
            index_level: 索引级别 SCI/SSCI/CSSCI/EI

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
        """列出所有工作空间（按创建时间倒序）"""
        reg = self._load_registry()
        workspaces = sorted(
            reg["workspaces"],
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )
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

        # 自动触发向量化索引
        index_result = self.trigger_indexing(ws_id, force=True)

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

            # 自动触发向量化（单文件上传后增量索引）
            index_result = self.trigger_indexing(ws_id, force=False)
        else:
            index_result = None

        return {
            "workspace_id": ws_id,
            "filename": filename,
            "path": str(target_path.relative_to(ws_path)),
            "size": len(content),
            "index_result": index_result,
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
        except Exception as e:
            stats = {"error": str(e), "files": 0, "chunks": 0, "skipped": 0}

        # 保存索引统计
        index_stats = {
            "workspace_id": ws_id,
            "indexed": True,
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
            indexed=True,
            index_stats=index_stats,
        )
        self._update_meta(
            ws_id,
            indexed=True,
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
        env_path = Path(".env")
        if not env_path.exists():
            env_path = Path(__file__).resolve().parent.parent.parent / ".env"

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
    file_hash TEXT NOT NULL
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
        self._conn.commit()

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
    ) -> None:
        """存储文档块（含向量）"""
        self._conn.execute(
            """INSERT INTO file_index
               (file_path, start_line, end_line, content, embedding, file_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_path, start_line, end_line, content, embedding, file_hash),
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
