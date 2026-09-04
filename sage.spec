# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Sage.

用法:
    pyinstaller --noconfirm --clean sage.spec

输出:
    Windows: dist/sage.exe
    macOS:   dist/sage
    Linux:   dist/sage

注意:
- 入口为 `sage.cli:app`（即 `sage` 命令入口，由 pyproject.toml 的 [project.scripts] 定义）
- sentence-transformers 模型文件已预打包到 _MEIPASS/models/，桌面端无需联网下载
- pymupdf / python-docx 已通过 [paper] extras 显式安装
"""

import sys
import atexit
import importlib.util
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# ── 打包前清空 .env（避免泄露开发环境的密钥）─────────────
# 打包产物中不包含任何 API Key 等敏感信息。
# 用户首次启动时会在 %LOCALAPPDATA%/Sage/.env 创建自己的配置，
# 该路径与程序安装目录隔离，升级重装不会覆盖用户配置。
_project_root = Path(SPECPATH).resolve() if 'SPECPATH' in dir() else Path(__file__).resolve().parent
_env_file = _project_root / ".env"
_env_backup = None
if _env_file.exists():
    _env_backup = _env_file.read_text(encoding="utf-8")
    _env_file.write_text("", encoding="utf-8")
    print("[sage.spec] 已临时清空 .env（打包完成后自动恢复）")
    # 注册恢复函数：PyInstaller 执行完 spec 后调用 atexit
    def _restore_env():
        if _env_backup is not None:
            _env_file.write_text(_env_backup, encoding="utf-8")
            print("[sage.spec] 已恢复 .env 原始内容")
    atexit.register(_restore_env)

# ── 打包前清除开发数据（记忆统计/仪表盘/工作空间元数据）─────
# 避免开发环境的对话历史、token 统计、会话摘要、工作空间元数据被打入产物。
# 打包后自动恢复，开发数据不丢失；用户首次启动时在自己的数据目录重建空库。
_dev_memory_db = _project_root / "data" / "memory.db"
_memory_db_backup = None
if _dev_memory_db.exists():
    _memory_db_backup = _dev_memory_db.read_bytes()
    _dev_memory_db.unlink()
    print("[sage.spec] 已临时移除开发数据 memory.db（打包后自动恢复）")

_workspaces_registry = _project_root / "workspaces" / "registry.json"
_registry_backup = None
if _workspaces_registry.exists():
    _registry_backup = _workspaces_registry.read_text(encoding="utf-8")
    _workspaces_registry.write_text('{\n  "workspaces": [],\n  "version": "1.0"\n}', encoding="utf-8")
    print("[sage.spec] 已临时清空 workspaces/registry.json（打包后自动恢复）")

def _restore_dev_data():
    if _memory_db_backup is not None and not _dev_memory_db.exists():
        _dev_memory_db.parent.mkdir(parents=True, exist_ok=True)
        _dev_memory_db.write_bytes(_memory_db_backup)
        print("[sage.spec] 已恢复开发数据 memory.db")
    if _registry_backup is not None:
        _workspaces_registry.write_text(_registry_backup, encoding="utf-8")
        print("[sage.spec] 已恢复 workspaces/registry.json")
atexit.register(_restore_dev_data)

# ── 收集隐式数据 / 元数据 ────────────────────────────────
# sentence-transformers / huggingface-hub 依赖大量动态元数据
datas = []
# sentence-transformers 为可选依赖（[embed]）：未安装时跳过相关收集，
# 打包产物仍可正常启动，仅本地向量索引功能不可用
_has_sentence_transformers = importlib.util.find_spec("sentence_transformers") is not None
if _has_sentence_transformers:
    datas += copy_metadata("sentence-transformers")
    datas += copy_metadata("huggingface-hub")
    datas += copy_metadata("transformers")
    datas += copy_metadata("tokenizers")
    datas += copy_metadata("safetensors")
    datas += copy_metadata("torch")
else:
    print("[sage.spec] 警告: 未安装 sentence-transformers（可选依赖 'sage-paper[embed]'），"
          "打包产物将不含本地向量索引功能")
datas += copy_metadata("numpy")
datas += copy_metadata("tiktoken")

# Typer / Rich 的 resources
datas += collect_data_files("rich")
datas += collect_data_files("typer")

# ── OCR 引擎数据文件（rapidocr_onnxruntime + onnxruntime）──
# config.yaml 和 models/*.onnx 是运行时必需的，PyInstaller 静态分析无法发现
datas += collect_data_files("rapidocr_onnxruntime")
datas += collect_data_files("onnxruntime")
binaries = collect_dynamic_libs("onnxruntime")

# ── 前端构建产物（web/dist）──────────────────────────────
# 打包后 sage.exe 通过 _MEIPASS/web/dist/ 提供前端界面
_web_dist = Path("web/dist")
if _web_dist.exists():
    datas.append((str(_web_dist), "web/dist"))

# ── 智能体定义文件（agents/*/agent.json + skill/）─────────
# AgentLoader 通过 __file__ 路径查找，打包后 __file__ 在 _MEIPASS/sage/agents/
# 所以 data 目标路径必须与 __file__ 解析出的路径一致
_agents_src = Path("src/sage/agents")
if _agents_src.exists():
    for agent_dir in _agents_src.iterdir():
        if not agent_dir.is_dir():
            continue
        agent_json = agent_dir / "agent.json"
        if agent_json.exists():
            dest = f"sage/agents/{agent_dir.name}"
            datas.append((str(agent_json), dest))
        # 智能体专属技能
        skill_dir = agent_dir / "skill"
        if skill_dir.exists():
            skill_json = skill_dir / "skill.json"
            if skill_json.exists():
                dest = f"sage/agents/{agent_dir.name}/skill"
                datas.append((str(skill_json), dest))

# ── 技能系统文件（.agent/skills/）──────────────────────
# SkillLoader 通过 __file__ 路径查找 .agent/skills/
_skills_src = Path(".agent/skills")
if _skills_src.exists():
    for skill_dir in _skills_src.iterdir():
        if not skill_dir.is_dir():
            continue
        for f in skill_dir.iterdir():
            if f.is_file():
                dest = f".agent/skills/{skill_dir.name}"
                datas.append((str(f), dest))

# ── 版本更新配置（GitCode API 凭据）────────────────────
# 独立于 .env 的配置文件，打包时不清空，供桌面端检查更新使用。
# api.py 通过 __file__ 路径定位（打包后在 _MEIPASS/sage/update_config.json）
_update_cfg = Path("src/sage/update_config.json")
if _update_cfg.exists():
    datas.append((str(_update_cfg), "sage"))

# ── HuggingFace 模型文件（预打包，避免桌面端首次使用时联网下载）──
# 将已缓存的 embedding 和 reranker 模型打包到 _MEIPASS/models/
# index.py 的 LocalEmbedder/CrossEncoderReranker 通过 refs/main 定位 snapshot 并加载
_hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
_pretrained_models = [
    "models--BAAI--bge-small-zh-v1.5",
    "models--BAAI--bge-reranker-base",
]
for _model_dir_name in _pretrained_models:
    _model_src = _hf_cache / _model_dir_name
    if not _model_src.exists():
        print(f"[sage.spec] 警告: 模型未缓存，跳过打包: {_model_dir_name}")
        continue

    # 打包 refs/main（用于定位正确的 snapshot hash）
    _refs_main = _model_src / "refs" / "main"
    if _refs_main.exists():
        datas.append((str(_refs_main), f"models/{_model_dir_name}/refs"))

    # 只打包 refs/main 指向的 snapshot，跳过其他历史 snapshot（节省空间）
    _snapshot_hash = None
    if _refs_main.exists():
        _snapshot_hash = _refs_main.read_text(encoding="utf-8").strip()
    if _snapshot_hash:
        _snapshot_src = _model_src / "snapshots" / _snapshot_hash
        if _snapshot_src.is_dir():
            for _f in _snapshot_src.rglob("*"):
                if _f.is_file():
                    _rel = _f.relative_to(_snapshot_src)
                    datas.append((str(_f), f"models/{_model_dir_name}/snapshots/{_snapshot_hash}/{_rel.parent}"))
            print(f"[sage.spec] 已打包模型: {_model_dir_name} (snapshot: {_snapshot_hash[:12]}...)")
        else:
            print(f"[sage.spec] 警告: snapshot 目录不存在: {_snapshot_hash}")
    else:
        # 回退：无 refs/main 时打包所有 snapshot（兼容旧缓存）
        _snapshots_dir = _model_src / "snapshots"
        if _snapshots_dir.exists():
            for _f in _snapshots_dir.rglob("*"):
                if _f.is_file():
                    _rel = _f.relative_to(_snapshots_dir)
                    datas.append((str(_f), f"models/{_model_dir_name}/snapshots/{_rel.parent}"))
            print(f"[sage.spec] 已打包模型（无 refs/main）: {_model_dir_name}")

# ── 隐式导入（PyInstaller 静态分析可能漏掉的动态导入）───
hiddenimports = []
hiddenimports += collect_submodules("sage")
if _has_sentence_transformers:
    hiddenimports += collect_submodules("sentence_transformers")
    hiddenimports += collect_submodules("huggingface_hub")
    hiddenimports += collect_submodules("transformers")
    hiddenimports += collect_submodules("tokenizers")
    hiddenimports += collect_submodules("torch")
    hiddenimports += collect_submodules("scipy")
    hiddenimports += collect_submodules("sklearn")
hiddenimports += collect_submodules("tiktoken")
hiddenimports += collect_submodules("fitz")        # pymupdf
hiddenimports += collect_submodules("docx")       # python-docx
# OCR 引擎子模块（rapidocr_onnxruntime 动态导入各子包）
hiddenimports += collect_submodules("rapidocr_onnxruntime")
hiddenimports += collect_submodules("onnxruntime")
hiddenimports += [
    "openai",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "fastapi",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "duckduckgo_search",
    "pydantic",
    "pydantic_settings",
    "dotenv",
]

# ── 排除不需要的重量级模块（显著减小体积） ──────────────
excludes = [
    "tkinter",
    "matplotlib",
    "pytest",
    "tests",
    "IPython",
    "jupyter",
    "notebook",
    "pandas",
    "PIL.ImageQt",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "setuptools._distutils",
]

block_cipher = None

a = Analysis(
    ["src/sage/cli.py"],
    pathex=[str(Path(".").resolve())],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 单文件（onefile）打包，运行时自解压到临时目录
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="sage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # 默认关闭 UPX，避免 CI runner 上不可用
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,             # 后端是 CLI 服务，必须保留控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
