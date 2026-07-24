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
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

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

# ── 隐式导入（PyInstaller 静态分析可能漏掉的动态导入）───
hiddenimports = []
hiddenimports += collect_submodules("sage")
hiddenimports += collect_submodules("sentence_transformers")
hiddenimports += collect_submodules("huggingface_hub")
hiddenimports += collect_submodules("transformers")
hiddenimports += collect_submodules("tiktoken")
hiddenimports += collect_submodules("fitz")        # pymupdf
hiddenimports += collect_submodules("docx")       # python-docx
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
    "scipy",
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
    binaries=[],
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
