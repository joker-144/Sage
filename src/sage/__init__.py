"""
Sage — 多智能体协作的学术论文写作辅助系统

核心能力:
  - 多智能体协作（主编 + 平等协作 + 整理汇报 + 多重验证）
  - 文献索引与语义检索（本地 sentence-transformers）
  - 文档处理（PDF/Word/LaTeX/扫描版OCR）
  - 写作辅助（大纲/段落/润色/逻辑检查）
  - 外部学术检索（Scholar/arXiv/CrossRef/Semantic Scholar）
  - 降AI味改写（规避AI检测）
  - 多工作空间管理（时间戳+领域标签命名）

多智能体角色:
  - 主编 (Supervisor): 任务拆解与质量把关
  - 文献调研员 (Literature): 文献检索与综述
  - 方法论专家 (Planner): 研究方法设计
  - 撰写员 (Coder): 论文撰写
  - 引用管理员 (Citation): 引用与格式化
  - 整理汇报员 (Consolidator): 整合产出
  - 审校核查员 (Reviewer): 多重验证
  - 修订员 (Debugger): 根据审校报告修复

支持期刊:
  SCI / SSCI / CSSCI / EI 多学科

架构层次:
  1. 基础模型层: DeepSeek / Qwen / OpenAI (Provider 可切换)
  2. 开发框架层: 自研 Agentic Loop + 多 Agent 协同编排
  3. 记忆与上下文层: SQLite + 向量 Embedding
  4. 工具与集成层: 18 Sage 工具 + 技能系统
  5. 多 Agent 协同层: 主控 + 平等协作 + 整理汇报 + 多重验证
  6. 运维与治理层: 可观测性 + 弹性重试 + 断路器
"""
from __future__ import annotations

import os
from pathlib import Path


def _read_version() -> str:
    """读取统一版本号 — 优先级：VERSION 文件 > 环境变量 > 已安装包 > 默认值"""
    # 1. VERSION 文件（项目根目录与打包后的 _MEIPASS 都尝试）
    for candidate in (
        Path(__file__).parent.parent.parent / "VERSION",
        Path(__file__).parent.parent / "VERSION",
    ):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            pass

    # 2. 环境变量（Electron 主进程可通过此注入）
    env_v = os.environ.get("SAGE_VERSION") or os.environ.get("Sage_VERSION")
    if env_v:
        return env_v.strip()

    # 3. 已安装包的 metadata
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("sage-paper")
    except (PackageNotFoundError, ImportError):
        pass

    # 4. 兜底
    return "1.0.0"


__version__ = _read_version()
