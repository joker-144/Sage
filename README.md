# Sage — 多智能体协作的学术论文写作辅助系统

> 面向 **SCI / SSCI / CSSCI / EI** 等高水平期刊与会议的论文写作辅助系统，由 8 个专业智能体协同完成从选题、文献调研、方法设计、撰写、引用管理到审校核查的完整写作流程。

---

## 目录

- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [版本管理](#版本管理)
- [配置说明](#配置说明)
- [多智能体角色](#多智能体角色)
- [技能包（Skill Packages）](#技能包skill-packages)
- [工具集](#工具集)
- [工作空间管理](#工作空间管理)
- [CLI 命令](#cli-命令)
- [HTTP API](#http-api)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [许可协议](#许可协议)

---

## 核心特性

- **多智能体协作**：8 个角色分工（主编 / 文献调研员 / 方法论专家 / 撰写员 / 引用管理员 / 整理汇报员 / 审校核查员 / 修订员），采用"主控 + 平等协作 + 整理汇报 + 多重验证"协作模式。
- **本地文献索引**：基于 `sentence-transformers`（all-MiniLM-L6-v2，384 维）构建向量化索引，语义检索已上传文献库，无需依赖外部 API。
- **多文档解析**：支持 PDF / Word / LaTeX / 扫描版 OCR，自动提取标题、作者、年份、DOI、摘要、关键词等元数据。
- **外部学术检索**：集成 Google Scholar / arXiv / CrossRef / Semantic Scholar 四大学术数据源，用于补充检索与引用真实性验证。
- **多格式引用管理**：支持 APA / MLA / GB-T7714 / Vancouver / Chicago / IEEE 六大引用格式，自动插入与格式化。
- **降 AI 味改写**：识别并改写 AI 生成文本的典型痕迹，规避 AI 检测，保留原意与引用。
- **多工作空间管理**：按"时间戳_领域标签"命名（如 `20260721_143022_CS-AI`），每个工作空间独立 SQLite 索引库，互不污染。
- **反思与自我修正**：工具执行后自动反思，失败自动修正（启发式规则 + 重试上限 + 断路器保护）。
- **三层记忆系统**：工作记忆（对话上下文）+ 长期记忆（经验教训）+ 语义记忆（向量检索）。
- **可观测性**：内置指标采集与日志，支持工具调用追踪与失败分析。
- **Provider 可切换**：通过 OpenAI 兼容协议接入 DeepSeek / Qwen / OpenAI 等服务商，无需修改代码。

---

## 系统架构

Sage 采用 6 层架构，自底向上：

| 层次 | 模块 | 职责 |
|------|------|------|
| **1. 基础模型层** | `llm/client.py` | OpenAI 兼容协议接入 LLM，支持 function calling 与流式输出 |
| **2. 开发框架层** | `agent/loop.py`, `agent/system_prompt.py` | 自研 Agentic Loop（思考 → 调用工具 → 观察结果 → 继续思考） |
| **3. 记忆与上下文层** | `memory/`, `context/` | SQLite 持久化 + 向量 Embedding + 对话历史压缩 |
| **4. 工具与集成层** | `tools/`, `skill_system.py` | 18+ Sage 专用工具 + 技能系统 + SkillHub 远程技能下载 |
| **5. 多 Agent 协同层** | `agents/` | 8 个角色智能体 + 平等协作讨论 + 整理汇报 + 多重验证 |
| **6. 运维与治理层** | `core/observability.py`, `core/resilience.py`, `core/mcp.py` | 可观测性 + 弹性重试 + 断路器 + MCP 协议支持 |

### 协作流程

```
用户写作需求
     │
     ▼
┌──────────┐  拆解任务   ┌─────────────────────────────────────┐
│  主编    │ ──────────► │  平等协作子智能体（交叉讨论）       │
│ (Supervisor) │         │  ┌──────────┐ ┌──────────┐ ┌──────┐│
└──────────┘             │  │文献调研员│ │方法论专家│ │撰写员││
     │                   │  └──────────┘ └──────────┘ └──────┘│
     │                   └─────────────────┬───────────────────┘
     │                                     ▼
     │                              ┌──────────────┐
     │                              │ 整理汇报员   │ 整合产出
     │                              └──────┬───────┘
     │                                     ▼
     │                              ┌──────────────┐
     │                              │ 引用管理员   │ 引用插入/格式化/查重
     │                              └──────┬───────┘
     │                                     ▼
     │                              ┌──────────────┐
     │ ◄────── 审校报告 ─────────── │ 审校核查员   │ 多重验证
     │                              └──────┬───────┘
     │                                     │ 有问题
     │                                     ▼
     │                              ┌──────────────┐
     │                              │ 修订员       │ 修复
     │                              └──────────────┘
     ▼
 最终论文输出
```

---

## 版本管理

Sage 采用**单一来源（single source of truth）**版本号管理：项目根目录的 [`VERSION`](VERSION) 文件是唯一权威，所有其他位置自动从它同步。

### 同步拓扑

```
              ┌─────────────────────┐
              │  /VERSION  (0.5.0)  │   ← 唯一权威
              └──────────┬──────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Python 后端  │  │  前端构建    │  │  Electron    │
│ sage.__ver__ │  │  Vite        │  │  主进程      │
│  (回退读取)  │  │  __APP_VER__ │  │  SAGE_VER    │
└──────────────┘  └──────────────┘  └──────────────┘
        │                │                │
        ▼                ▼                ▼
    pyproject.toml   web/package.json  web/electron/main.cjs
   (dynamic version)  (prebuild 同步)   (兜底版本)
```

### 显示位置

| 位置 | 来源 |
|------|------|
| 后端 `sage.__version__` | `src/sage/__init__.py` 读取 VERSION 文件 |
| `pyproject.toml` | `dynamic = ["version"]` 关联到 `sage.__version__` |
| 前端侧边栏 | `__APP_VERSION__`（Vite `define` 注入） |
| 前端底部状态栏 | `__APP_VERSION__` |
| 仪表盘 API 版本 | `/health` 端点返回的 `version` |
| 设置面板"当前版本" | `/api/version/check` 端点 |
| Electron 桌面端 | `SAGE_VERSION` 环境变量注入 |
| CLI `sage version` | 后端 `__version__` |

### 提升版本号

```bash
# 查看当前版本
python scripts/bump_version.py show

# Patch 升级（0.5.0 -> 0.5.1）
python scripts/bump_version.py patch

# Minor 升级（0.5.0 -> 0.6.0）
python scripts/bump_version.py minor

# Major 升级（0.5.0 -> 1.0.0）
python scripts/bump_version.py major

# 预发布版本（0.5.0 -> 0.5.0-rc.1）
python scripts/bump_version.py pre --tag rc

# 直接设置
python scripts/bump_version.py set 1.2.3
python scripts/bump_version.py set 1.2.3-beta.1
```

`bump_version.py` 会自动：
1. 更新 `VERSION` 文件
2. 向 `CHANGELOG.md` 追加新版本条目（若存在）
3. 调用 `sync_version.py` 级联同步到 `web/package.json`、`web/electron/main.cjs` 等位置

### 手动同步（仅在需要时使用）

```bash
# 自动检测并修复不一致项
python scripts/sync_version.py

# 仅检查，不修改（CI 用）
python scripts/sync_version.py --check
```

### 前端构建时自动同步

`web/package.json` 已配置 prebuild/prepack/predev 钩子：

```json
{
  "scripts": {
    "prebuild": "python ../scripts/sync_version.py",
    "prepack": "python ../scripts/sync_version.py",
    "predev": "python ../scripts/sync_version.py"
  }
}
```

`npm run build` / `npm run dev` / `npm pack` 会**自动调用同步脚本**，无需手动干预。

### CI 集成

在 CI 流水线中加入版本一致性检查：

```yaml
- name: Check version consistency
  run: python scripts/sync_version.py --check
```

---

## 快速开始

### 环境要求

- Python ≥ 3.11
- 操作系统：Windows / macOS / Linux

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd Sage

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 安装核心依赖
pip install -e .

# （可选）安装论文文档解析依赖
pip install -e ".[paper]"

# （可选）安装开发与测试依赖
pip install -e ".[dev]"
```

### 首次配置

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env，至少填入 LLM_CHAT_API_KEY
# 推荐使用 DeepSeek（性价比高）：https://platform.deepseek.com/api_keys
```

或使用交互式配置向导：

```bash
sage init
```

### 启动服务

```bash
# 启动 API 服务（默认 http://127.0.0.1:8000）
sage serve

# 或进入交互式对话
sage chat
```

### 创建你的第一个论文工作空间

```bash
# 通过 API 创建工作空间
curl -X POST http://127.0.0.1:8000/api/sage/workspaces \
  -H "Content-Type: application/json" \
  -d '{"domain_tag": "CS-AI", "description": "人工智能方向论文", "index_level": "SCI"}'

# 上传论文
curl -X POST http://127.0.0.1:8000/api/sage/workspaces/<ws_id>/upload \
  -F "file=@paper.pdf" \
  -F "subdir=papers"

# 触发向量化索引（上传后会自动触发，此为手动重建）
curl -X POST "http://127.0.0.1:8000/api/sage/workspaces/<ws_id>/index?force=true"

# 开始对话写作
sage chat "帮我基于已上传的文献写一段关于 Transformer 注意力机制的研究背景"
```

---

## 配置说明

所有配置通过 `.env` 文件管理（参考 `.env.example`）：

### 对话 LLM 配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `LLM_CHAT_API_KEY` | LLM 服务商 API Key（必填） | — |
| `LLM_CHAT_BASE_URL` | API Base URL | `https://api.deepseek.com` |
| `LLM_CHAT_MODEL` | 模型名称 | `deepseek-chat` |
| `LLM_CHAT_TEMPERATURE` | 采样温度 | `0.3` |
| `LLM_CHAT_MAX_TOKENS` | 单次生成最大 token | `8192` |
| `LLM_CHAT_TIMEOUT` | 请求超时（秒） | `120.0` |
| `LLM_CHAT_STREAMING` | 是否流式输出 | `true` |
| `LLM_CHAT_MAX_TOOL_ROUNDS` | 单轮对话最大工具调用次数 | `20` |

### Embedding 配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `LLM_EMBEDDING_MODEL` | 本地 Embedding 模型 | `sentence-transformers/all-MiniLM-L6-v2` |
| `HF_ENDPOINT` | HuggingFace 镜像（国内推荐） | `https://hf-mirror.com` |

### 记忆与工作空间

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `MEMORY_SQLITE_PATH` | 全局记忆数据库路径 | `data/memory.db` |
| `DEV_AGENT_WORKSPACE` | 默认工作空间（留空使用当前目录） | `.` |
| `DEV_AGENT_MAX_CONTEXT_TOKENS` | 上下文窗口 token 上限 | `60000` |
| `DEV_AGENT_SUMMARY_TRIGGER_TOKENS` | 触发摘要压缩的阈值 | `45000` |

### 可选外部检索

| 环境变量 | 说明 |
|----------|------|
| `TAVILY_API_KEY` | Tavily AI 高质量搜索 API Key（每月 1000 次免费），获取地址：https://tavily.com |

---

## 多智能体角色

8 个智能体定义在 [`src/sage/agents/`](src/sage/agents/) 下，每个角色有独立的 `agent.json` 与可选的专属技能：

| 角色 | 英文名 | 目录 | 职责 |
|------|--------|------|------|
| 主编 | Orchestrator | `supervisor/` | 任务拆解、子智能体调度、流程控制、质量把关 |
| 文献调研员 | Literature | `literature/` | 文献检索、综述、研究现状分析 |
| 方法论专家 | Methodology | `planner/` | 研究方法设计、实验方案、论证框架 |
| 撰写员 | Writer | `coder/` | 论文各章节具体撰写 |
| 引用管理员 | Citation | `citation/` | 引用插入、参考文献格式化、查重检测 |
| 整理汇报员 | Consolidator | `consolidator/` | 整合各子智能体讨论产出 |
| 审校核查员 | Verifier | `reviewer/` | 多重验证（文献库 + 逻辑 + 外部检索 + 学术规范） |
| 修订员 | Reviser | `debugger/` | 根据审校报告修复问题 |

智能体定义加载通过 [`sage.agents.loader.AgentLoader`](src/sage/agents/loader.py) 实现，编排逻辑在 [`sage.agents.orchestrator.AgentOrchestrator`](src/sage/agents/orchestrator.py) 中。

---

## 技能包（Skill Packages）

5 个 Sage 专用技能包位于 [`.agent/skills/`](.agent/skills/)，每个技能包含 `skill.json` 元数据与触发条件：

| 技能包 | 描述 | 工具 |
|--------|------|------|
| **paper-processing** | PDF/Word/LaTeX/扫描版文档解析、元数据提取、OCR | `parse_pdf`, `parse_docx`, `parse_latex`, `extract_metadata`, `ocr_document` |
| **literature-index** | 文献向量索引、语义检索、引用管理、查重检测 | `index_papers`, `search_literature`, `extract_references`, `insert_citation`, `format_references`, `check_plagiarism` |
| **writing-assistant** | 大纲生成、段落撰写、学术润色、逻辑检查 | `generate_outline`, `write_paragraph`, `polish_academic`, `check_logic` |
| **external-search** | Google Scholar / arXiv / CrossRef / Semantic Scholar 外部学术检索 | `search_scholar`, `search_arxiv`, `search_crossref`, `search_semantic_scholar` |
| **ai-pattern-reducer** | 降 AI 味改写，规避 AI 检测 | `reduce_ai_pattern` |

技能加载通过 [`sage.skill_system.SkillLoader`](src/sage/skill_system.py) 实现，远程技能搜索/下载通过内置的 [`sage.skill_hub_client.SkillHubClient`](src/sage/skill_hub_client.py)（不依赖外部 CLI）。

---

## 工具集

所有工具通过 [`sage.tools.engine.ToolEngine`](src/sage/tools/engine.py) 统一调度，遵循 OpenAI function calling schema：

### 通用文件操作（[`tools/file_ops.py`](src/sage/tools/file_ops.py)）

- `read_file` — 读取文件内容（支持行号范围）
- `write_file` — 创建或覆写文件
- `edit_file` — 搜索-替换精准编辑
- `list_dir` — 列出目录内容

### Sage 专用论文工具（[`tools/paper_ops.py`](src/sage/tools/paper_ops.py)）

- `index_papers` — 对工作空间论文建立向量索引
- `search_literature` — 语义检索文献库
- `extract_references` — 提取参考文献列表
- `insert_citation` — 在指定位置插入引用
- `format_references` — 按目标期刊格式化参考文献
- `check_plagiarism` — 查重检测
- `parse_pdf` / `parse_docx` / `parse_latex` — 文档解析
- `extract_metadata` — 提取论文元数据
- `ocr_document` — OCR 识别
- `generate_outline` / `write_paragraph` / `polish_academic` / `check_logic` — 写作辅助
- `reduce_ai_pattern` — 降 AI 味改写
- `search_scholar` / `search_arxiv` / `search_crossref` / `search_semantic_scholar` — 外部学术检索

### 通用技能与网络（[`tools/skill_ops.py`](src/sage/tools/skill_ops.py), [`tools/web.py`](src/sage/tools/web.py)）

- `list_skills` / `load_skill` / `install_skill` — 技能管理
- `web_search` — DuckDuckGo 搜索（免费免配置）
- `web_search_pro` — Tavily AI 高质量搜索（需 API Key）
- `web_fetch` — 抓取指定 URL 网页正文

工具返回值统一为 [`ToolResult`](src/sage/tools/types.py) 数据类，含 `success` / `output` / `data` / `error` / `metadata` 字段。

---

## 工作空间管理

Sage 支持多工作空间，按"时间戳_领域标签"命名，每个工作空间独立隔离：

### 命名规则

```
workspaces/
├── registry.json                       ← 全局注册表
├── 20260721_143022_CS-AI/              ← 工作空间 ID
│   ├── .sage/
│   │   ├── meta.json                   ← 元数据（创建时间/领域/描述）
│   │   ├── index_stats.json            ← 索引统计
│   │   └── index.db                    ← 独立 SQLite 索引库
│   ├── papers/                         ← 用户上传/导入的论文
│   └── drafts/                         ← 生成的论文草稿
└── 20260721_150000_MED-Cardio/
    └── ...
```

### 领域标签规范

- 仅允许字母 / 数字 / 连字符 / 下划线
- 长度 2-32 字符
- 推荐格式：`<学科大类>-<子方向>`，如 `CS-AI`、`MED-Cardio`、`SSCI-PSY`

### 索引级别

支持 `SCI` / `SSCI` / `CSSCI` / `EI` 四种索引级别，影响引用格式默认值与质量校验严格度。

### 隔离设计

- 每个工作空间使用独立的 SQLite 数据库（`.sage/index.db`）
- 通过 [`WorkspaceStore`](src/sage/workspace_manager.py) 隔离，**不污染全局 `MemoryStore` 单例**
- 工作空间切换通过 `switch_to()` 更新 `cfg.workspace`，不修改原有 API 接口

---

## CLI 命令

通过 `sage <command>` 调用（基于 Typer + Rich）：

| 命令 | 说明 |
|------|------|
| `sage init` | 首次配置向导（选择 Provider、填入 API Key、生成 .env） |
| `sage chat [PROMPT]` | 交互式对话（不传参进入 REPL，传参单次执行后退出） |
| `sage serve` | 启动 HTTP API 服务（默认 `127.0.0.1:8000`） |
| `sage index` | 索引当前工作空间的论文 |
| `sage stats` | 显示系统统计（记忆 / 索引 / 工具调用） |
| `sage collaborate` | 多智能体协作演示 |
| `sage version` | 显示版本信息并与 PyPI 对比 |
| `sage update` | 自动升级到最新版本 |

### chat 模式内置命令

在 `sage chat` 交互模式下可使用：

- `/help` — 查看可用命令
- `/clear` — 清空当前上下文
- `/tokens` — 查看当前 token 占用
- `/index` — 触发工作空间索引
- `/stats` — 显示系统统计
- `/exit` 或 `/quit` — 退出

---

## HTTP API

启动 `sage serve` 后，所有 API 默认监听 `http://127.0.0.1:8000`。完整 OpenAPI 文档访问 `/docs`。

### Sage 工作空间 API（11 个）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sage/workspaces` | 创建工作空间 |
| GET | `/api/sage/workspaces` | 列出所有工作空间 |
| GET | `/api/sage/workspaces/{ws_id}` | 获取工作空间详情 |
| DELETE | `/api/sage/workspaces/{ws_id}` | 删除工作空间 |
| POST | `/api/sage/workspaces/{ws_id}/import-folder` | 从文件夹批量导入论文 |
| POST | `/api/sage/workspaces/{ws_id}/upload` | 上传单个论文文件 |
| POST | `/api/sage/workspaces/{ws_id}/index` | 触发向量化索引 |
| GET | `/api/sage/workspaces/{ws_id}/index-status` | 查询索引状态 |
| POST | `/api/sage/workspaces/{ws_id}/switch` | 切换到该工作空间 |
| GET | `/api/sage/workspaces/{ws_id}/papers` | 列出工作空间中的论文 |

### Sage 论文工具 API（7 个）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sage/search` | 语义检索工作空间文献库 |
| POST | `/api/sage/extract-references` | 从论文提取参考文献 |
| POST | `/api/sage/format-references` | 按目标期刊格式化参考文献 |
| POST | `/api/sage/check-plagiarism` | 查重检测 |
| POST | `/api/sage/search-external` | 外部学术数据源检索 |
| GET | `/api/sage/citation-styles` | 获取支持的引用格式列表 |

### 通用 Agent API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/stream` | 流式对话（SSE） |
| GET | `/conversations` | 列出历史对话 |
| POST | `/conversations` | 创建新对话 |
| GET | `/conversations/{id}/messages` | 获取对话消息 |
| DELETE | `/conversations/{id}` | 删除对话 |
| POST | `/index` | 索引当前工作空间 |
| GET | `/api/agent/info` | Agent 信息 |
| GET | `/api/agents` | 列出所有智能体角色 |
| GET | `/api/tools` | 列出所有工具 |
| GET | `/api/skills` | 列出已安装技能 |
| GET | `/api/skills/remote-search` | 远程搜索技能 |
| POST | `/api/skills/install` | 安装远程技能 |
| GET | `/api/skills/manifest` | 技能清单 |
| GET | `/api/models` | 可用模型列表 |
| GET/POST | `/api/user-settings` | 用户设置 |
| GET | `/api/version/check` | 检查版本更新 |
| POST | `/api/version/download` | 下载新版本 |
| POST | `/api/version/install` | 安装新版本 |
| GET | `/memory/stats` | 记忆系统统计 |
| GET | `/memory/memories` | 列出长期记忆 |
| POST | `/memory/search` | 语义检索记忆 |
| GET | `/memory/summaries` | 列出对话摘要 |
| GET | `/health` | 健康检查 |

---

## 项目结构

```
Sage/
├── src/sage/                       ← 主源码
│   ├── __init__.py                 ← 版本与系统说明
│   ├── api.py                      ← FastAPI 应用（所有 HTTP 接口）
│   ├── cli.py                      ← Typer CLI 入口
│   ├── config.py                   ← Pydantic Settings 配置
│   ├── workspace_manager.py        ← Sage 多工作空间管理
│   ├── skill_system.py             ← 技能系统本地加载
│   ├── skill_hub_client.py         ← SkillHub 远程客户端（内置）
│   │
│   ├── llm/                        ← LLM 客户端
│   │   └── client.py               ← OpenAI 兼容客户端（含 function calling）
│   │
│   ├── agent/                      ← Agentic Loop 框架
│   │   ├── loop.py                 ← Agent 主循环
│   │   └── system_prompt.py        ← 系统提示词模板
│   │
│   ├── agents/                     ← 多智能体协同层
│   │   ├── loader.py               ← 智能体定义加载器
│   │   ├── orchestrator.py         ← 多 Agent 编排器
│   │   ├── reflection.py           ← 反思与自我修正引擎
│   │   ├── supervisor/             ← 主编（含 agent.json）
│   │   ├── literature/             ← 文献调研员
│   │   ├── planner/                ← 方法论专家
│   │   ├── coder/                  ← 撰写员
│   │   ├── citation/               ← 引用管理员
│   │   ├── consolidator/           ← 整理汇报员
│   │   ├── reviewer/               ← 审校核查员
│   │   ├── debugger/               ← 修订员
│   │   └── assistant/              ← 通用助手（兼容 dev-agent）
│   │
│   ├── memory/                     ← 三层记忆系统
│   │   ├── store.py                ← SQLite 持久化（MemoryStore 单例）
│   │   ├── long_term.py            ← 长期记忆（经验教训）
│   │   ├── semantic.py             ← 语义记忆（向量检索）
│   │   └── memory_orch.py          ← 记忆编排器
│   │
│   ├── context/                    ← 上下文管理
│   │   ├── index.py                ← ProjectIndex 向量索引
│   │   ├── history.py              ← 对话历史与压缩
│   │   ├── manager.py              ← 上下文管理器
│   │   └── tokenizer.py            ← Token 计数
│   │
│   ├── tools/                      ← 工具集成层
│   │   ├── engine.py               ← 工具引擎（OpenAI schema）
│   │   ├── types.py                ← ToolResult 等数据类型
│   │   ├── file_ops.py             ← 通用文件操作
│   │   ├── paper_ops.py            ← Sage 论文工具
│   │   ├── skill_ops.py            ← 技能管理工具
│   │   └── web.py                  ← 网络搜索工具
│   │
│   └── core/                       ← 运维与治理
│       ├── observability.py        ← 可观测性
│       ├── resilience.py           ← 弹性重试 + 断路器
│       └── mcp.py                  ← MCP 协议支持
│
├── .agent/skills/                  ← 5 个 Sage 专用技能包
│   ├── paper-processing/
│   ├── literature-index/
│   ├── writing-assistant/
│   ├── external-search/
│   └── ai-pattern-reducer/
│
├── workspaces/                     ← Sage 工作空间根目录（运行时创建）
├── data/                           ← 全局记忆数据库
├── logs/                           ← 运行日志
├── tests/                          ← 测试（开发时使用）
├── web/                            ← Electron + Vue 前端（可选）
├── pyproject.toml                  ← 项目元数据与依赖
├── .env.example                    ← 配置模板
└── README.md                       ← 本文档
```

---

## 开发与测试

### 安装开发依赖

```bash
pip install -e ".[dev,paper]"
```

### 运行测试

```bash
# 运行全部测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_workspace_manager.py -v

# 运行指定测试
pytest tests/test_api.py::TestSageWorkspacesAPI::test_upload_file -v
```

### 测试覆盖范围

测试套件覆盖以下模块（共 218 个测试用例，全部通过）：

| 测试文件 | 覆盖模块 |
|----------|----------|
| `test_config.py` | `sage.config` — 配置加载与单例 |
| `test_memory.py` | `sage.memory.*` — 三层记忆系统 |
| `test_context_index.py` | `sage.context.index` — 向量索引与 Embedder |
| `test_tools.py` | `sage.tools.*` — 工具引擎、文件操作、论文工具、技能工具 |
| `test_skill_system.py` | `sage.skill_system` — 技能加载与元数据 |
| `test_agents.py` | `sage.agents.*` — 智能体加载、反思引擎、编排器 |
| `test_workspace_manager.py` | `sage.workspace_manager` — 多工作空间管理 |
| `test_api.py` | `sage.api` — 所有 HTTP API 端点 |
| `test_agent_loop.py` | `sage.agent.loop` — Agentic Loop 主循环 |

### 测试隔离设计

- 每个测试通过 `tmp_path` + `monkeypatch.chdir` 实现文件系统隔离
- 通过 `env_setup` 夹具显式设置所有环境变量，避免 `.env` 污染
- `reset_singletons` autouse 夹具在每个测试前后重置 `config` 和 `store` 单例
- 使用 `MockLLMClient` 避免真实 API 调用，加快测试速度

### 代码规范

- Python ≥ 3.11，使用 `from __future__ import annotations` 启用延迟注解
- 类型注解完备（Pydantic + dataclass）
- 中文 docstring 与注释
- 所有公开接口保持向后兼容

---

## 许可协议

MIT License

---

## 致谢

Sage 系统基于以下开源项目构建：

- [FastAPI](https://fastapi.tiangolo.com/) — Web 框架
- [Pydantic](https://pydantic.dev/) — 数据校验
- [Typer](https://typer.tiangolo.com/) — CLI 框架
- [Rich](https://rich.readthedocs.io/) — 终端美化
- [sentence-transformers](https://www.sbert.net/) — 本地 Embedding
- [OpenAI Python SDK](https://github.com/openai/openai-python) — LLM 客户端
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF 解析
- [python-docx](https://python-docx.readthedocs.io/) — Word 解析
