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
- sentence-transformers 会在首次运行时下载模型，本 spec 不打包模型文件
- pymupdf / python-docx 已通过 [paper] extras 显式安装
"""

import sys
import atexit
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
datas += copy_metadata("sentence-transformers")
datas += copy_metadata("huggingface-hub")
datas += copy_metadata("transformers")
datas += copy_metadata("tokenizers")
datas += copy_metadata("safetensors")
datas += copy_metadata("torch")
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

# ── 隐式导入（PyInstaller 静态分析可能漏掉的动态导入）───
hiddenimports = []
hiddenimports += collect_submodules("sage")
hiddenimports += collect_submodules("sentence_transformers")
hiddenimports += collect_submodules("huggingface_hub")
hiddenimports += collect_submodules("transformers")
hiddenimports += collect_submodules("tiktoken")
hiddenimports += collect_submodules("fitz")        # pymupdf
hiddenimports += collect_submodules("docx")       # python-docx
hiddenimports += collect_submodules("torch")
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("sklearn")
hiddenimports += collect_submodules("tokenizers")
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
