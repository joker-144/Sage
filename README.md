# Sage — 多智能体协作的学术论文写作辅助系统

> 面向 **SCI / SSCI / CSSCI / EI** 等高水平期刊与会议的论文写作辅助系统，由 8 个专业智能体协同完成从选题、文献调研、方法设计、撰写、引用管理到审校核查的完整写作流程。

**当前版本：0.6.5** · Python ≥ 3.11 · Windows / macOS / Linux / Electron 桌面端

---

## 目录

- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [系统架构](#系统架构)
- [写作模式](#写作模式)
- [多智能体角色](#多智能体角色)
- [技能包（Skill Packages）](#技能包skill-packages)
- [工具集](#工具集)
- [工作空间管理](#工作空间管理)
- [配置说明](#配置说明)
- [前端与桌面端](#前端与桌面端)
- [版本管理](#版本管理)
- [CLI 命令](#cli-命令)
- [HTTP API](#http-api)
- [开发与测试](#开发与测试)
- [许可协议](#许可协议)

---

## 核心特性

- **写作模式（智能选择流程）**：内置意图分析结构（规则快速判断 + LLM 精细分析），简单任务自动路由到匹配角色 Agent 或通用助手，复杂任务启动 8 智能体完整协作流程，兼顾效率与质量。
- **多智能体协作**：8 个角色分工（主编 / 文献调研员 / 方法论专家 / 撰写员 / 引用管理员 / 整理汇报员 / 审校核查员 / 修订员），采用"主控 + 平等协作 + 整理汇报 + 多重验证"协作模式。
- **思考内容输出**：自动捕获推理模型（如 DeepSeek-R1）的思考链（reasoning_content），以独立卡片展示，默认折叠，支持点击展开查看完整推理过程。
- **token 消耗透明**：工具调用、智能体调用、技能调用卡片后方实时显示该轮消耗的 token 总数，便于成本监控。
- **本地文献索引**：基于 `sentence-transformers`（all-MiniLM-L6-v2，384 维）构建向量化索引，语义检索已上传文献库，无需依赖外部 API。
- **多文档解析**：支持 PDF / Word / LaTeX / 扫描版 OCR，自动提取标题、作者、年份、DOI、摘要、关键词等元数据，并通过外部源（维普/万方/CrossRef）认证纠正期刊名等关键字段。
- **外部学术检索**：集成 Google Scholar / arXiv / CrossRef / Semantic Scholar 四大学术数据源，用于补充检索与引用真实性验证；额外集成 `search_cnki` 工具，通过维普/万方 web 检索 + CrossRef API 回退认证论文元数据（如纠正 PDF 提取中常见的期刊名/栏目名混淆问题）。
- **联网搜索（可选）**：集成 Tavily AI 搜索（`web_search_pro`），当 DuckDuckGo 免费搜索结果质量不高时自动切换，需配置 `TAVILY_API_KEY`（每月 1000 次免费额度）。
- **多格式引用管理**：支持 APA / MLA / GB-T7714 / Vancouver / Chicago / IEEE 六大引用格式，自动插入与格式化。
- **降 AI 味改写**：识别并改写 AI 生成文本的典型痕迹，规避 AI 检测，保留原意与引用。
- **多工作空间管理**：按"时间戳_领域标签"命名（如 `20260721_143022_CS-AI`），每个工作空间独立 SQLite 索引库，互不污染。
- **反思与自我修正**：工具执行后自动反思，失败自动修正（启发式规则 + 重试上限 + 断路器保护）。
- **三层记忆系统**：工作记忆（对话上下文）+ 长期记忆（经验教训）+ 语义记忆（向量检索）。
- **Provider 可切换**：通过 OpenAI 兼容协议接入 DeepSeek / Qwen / OpenAI / Anthropic / 智谱 / 月之暗面等，前端设置界面可视化切换，无需修改代码。
- **桌面端分发**：基于 Electron + NSIS 打包为 Windows 安装包，支持自动检查更新、镜像加速下载、静默安装后自启动。

---

## 快速开始

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd Sage

# 创建并激活虚拟环境
python -m venv .venv
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

也可启动服务后在前端设置界面配置（支持多 Provider 切换、API Key 管理、模型列表自动拉取）。

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

写作模式采用"意图分析 → 智能路由"的分层架构，简单任务直接由匹配角色 Agent 处理，复杂任务启动完整多智能体协作：

```
用户写作需求
     │
     ▼
┌──────────────────────────────────────────┐
│  意图分析（主编）                         │
│  1. 规则快速判断：明确简单/复杂直接路由  │
│  2. LLM 精细分析：不确定时调用 LLM 输出   │
│     {complexity, role, reason}            │
└──────────────────┬───────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
     简单任务                复杂任务
        │                     │
        ▼                     ▼
┌─────────────────┐  ┌──────────┐  拆解任务   ┌─────────────────────────────────────┐
│ 匹配角色 Agent  │  │  主编    │ ──────────► │  平等协作子智能体（交叉讨论）       │
│ ┌─────────────┐ │  │(Supervisor)│         │  ┌──────────┐ ┌──────────┐ ┌──────┐│
│ │literature   │ │  └──────────┘             │  │文献调研员│ │方法论专家│ │撰写员││
│ │planner      │ │                            │  └──────────┘ └──────────┘ └──────┘│
│ │coder        │ │                            └─────────────────┬───────────────────┘
│ │reviewer     │ │                                              ▼
│ │debugger     │ │                                       ┌──────────────┐
│ │citation     │ │                                       │ 整理汇报员   │ 整合产出
│ │consolidator │ │                                       └──────┬───────┘
│ └─────────────┘ │                                              ▼
│ 或通用助手兜底  │                                       ┌──────────────┐
└─────────────────┘                                       │ 引用管理员   │ 引用插入/格式化/查重
                                                          └──────┬───────┘
                                                                 ▼
                                                          ┌──────────────┐
                                   ◄────── 审校报告 ────── │ 审校核查员   │ 多重验证
                                                          └──────┬───────┘
                                                                 │ 有问题
                                                                 ▼
                                                          ┌──────────────┐
                                                          │ 修订员       │ 修复
                                                          └──────────────┘
        │                     │
        └──────────┬──────────┘
                   ▼
            最终论文输出
```

---

## 写作模式

写作模式（`mode=writing`）是 Sage 的核心工作模式，采用**意图分析 → 智能路由**的分层架构，根据任务复杂度自动选择单 Agent 或多智能体协作流程，兼顾效率与质量。

### 意图分析结构

意图分析由 [`AgentOrchestrator._analyze_intent()`](src/sage/agents/orchestrator.py) 实现，采用**规则 + LLM 混合**两阶段策略：

#### 第一层：规则快速判断（`_quick_classify`）

无需 LLM 调用，毫秒级响应：

| 判断规则 | 路由结果 |
|----------|----------|
| 包含完整论文写作关键词（论文/综述/多章节/SCI/SSCI/毕业论文等） | 复杂任务 → 多智能体协作 |
| 问候/简单对话（你好/谢谢等） | 简单任务 → 通用助手 |
| 短问题（<30字）且无写作/研究关键词 | 简单任务 → 匹配角色 Agent |
| 其他不确定情况 | 交给第二层 LLM 分析 |

简单任务的角色匹配通过 [`_match_role_by_keywords`](src/sage/agents/orchestrator.py) 实现，根据问题关键词匹配最合适的角色（如"文献"→literature、"方法"→planner、"撰写"→coder），无匹配返回 `general`（通用助手）。

#### 第二层：LLM 精细分析（`_analyze_intent_with_llm`）

规则无法确定时调用 LLM（不带工具，`max_tokens=200`），输出结构化 JSON：

```json
{
  "complexity": "simple",   // simple | complex
  "role": "literature",      // simple 时为最匹配角色；complex 时为 supervisor
  "reason": "查询文献相关研究"  // 判断理由（展示给用户）
}
```

LLM 分析失败时降级为通用助手处理，确保不中断服务。

### 路由结果

| 任务类型 | 处理方式 | 示例 |
|----------|----------|------|
| **简单任务** | 由意图分析选择匹配角色 Agent 处理，无匹配时用通用助手兜底 | "帮我检索 Transformer 相关文献" → literature |
| **复杂任务** | 启动完整多智能体协作流程（主编→文献→方法→撰写→整理→引用→审校→修订） | "写一篇关于注意力机制的完整论文" |

### 前端交互

写作模式通过前端按钮切换（默认关闭，点击开启），开启后：

- 用户发送消息时携带 `mode=writing` 参数
- 主编先进行意图分析，过程通过 `reflection` 事件展示：`[主编] 反思: 意图分析结果: 简单任务 → 文献调研员`
- 简单任务的回复带角色前缀：`**[文献调研员]** 内容`
- 复杂任务按完整协作流程执行，每个子智能体的进度通过 `collaborate` 事件实时展示

### 思考内容与 token 显示

- **思考内容**：自动捕获推理模型（DeepSeek-R1 等）的 `reasoning_content` 字段，通过 `reasoning` SSE 事件传递到前端，以紫色独立卡片展示，默认折叠，点击可展开查看完整推理过程
- **token 消耗**：每轮 LLM 调用的 token 用量通过 `tokens` 字段传递，在工具/智能体/技能调用卡片标题旁显示消耗的总 token 数

---

## 多智能体角色

8 个智能体定义在 [`src/sage/agents/`](src/sage/agents/) 下，每个角色有独立的 `agent.json` 与可选的专属技能。写作模式下还会动态使用通用助手（不绑定角色 prompt）处理无匹配角色的简单任务：

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
| 通用助手 | General | — | 写作模式下无匹配角色时的兜底 Agent，处理问答/解释/通用任务 |

智能体定义加载通过 [`sage.agents.loader.AgentLoader`](src/sage/agents/loader.py) 实现，编排逻辑在 [`sage.agents.orchestrator.AgentOrchestrator`](src/sage/agents/orchestrator.py) 中。意图分析与智能路由由 [`_analyze_intent`](src/sage/agents/orchestrator.py) 统一调度（详见 [写作模式](#写作模式) 章节）。

---

## 技能包（Skill Packages）

5 个 Sage 专用技能包位于 [`.agent/skills/`](.agent/skills/)，每个技能包含 `skill.json` 元数据与触发条件：

| 技能包 | 描述 | 工具 |
|--------|------|------|
| **paper-processing** | PDF/Word/LaTeX/扫描版文档解析、元数据提取、OCR | `parse_pdf`, `parse_docx`, `parse_latex`, `extract_metadata`, `ocr_document` |
| **literature-index** | 文献向量索引、语义检索、引用管理、查重检测 | `index_papers`, `search_literature`, `extract_references`, `insert_citation`, `format_references`, `check_plagiarism` |
| **writing-assistant** | 大纲生成、段落撰写、学术润色、逻辑检查 | `generate_outline`, `write_paragraph`, `polish_academic`, `check_logic` |
| **external-search** | Google Scholar / arXiv / CrossRef / Semantic Scholar 外部学术检索，含 CNKI 元数据认证 | `search_scholar`, `search_arxiv`, `search_crossref`, `search_semantic_scholar`, `search_cnki` |
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
- `parse_pdf` / `parse_docx` / `parse_latex` — 文档解析（`parse_pdf` 自动通过维普/万方/CrossRef 认证元数据）
- `extract_metadata` — 提取论文元数据
- `ocr_document` — OCR 识别
- `generate_outline` / `write_paragraph` / `polish_academic` / `check_logic` — 写作辅助
- `reduce_ai_pattern` — 降 AI 味改写
- `search_scholar` / `search_arxiv` / `search_crossref` / `search_semantic_scholar` — 外部学术检索
- `search_cnki` — 通过维普/万方 web 检索 + CrossRef API 回退认证论文元数据（期刊名、栏目、ISSN 等）

### 通用技能与网络（[`tools/skill_ops.py`](src/sage/tools/skill_ops.py), [`tools/web.py`](src/sage/tools/web.py)）

- `list_skills` / `load_skill` / `install_skill` — 技能管理
- `web_search` — DuckDuckGo 搜索（免费免配置，优先使用）
- `web_search_pro` — Tavily AI 高质量搜索（当 `web_search` 结果质量不高时使用，需配置 `TAVILY_API_KEY`）
- `web_fetch` — 抓取指定 URL 网页正文

工具返回值统一为 [`ToolResult`](src/sage/tools/types.py) 数据类，含 `success` / `output` / `data` / `error` / `metadata` 字段。

---

## 工作空间管理

Sage 支持多工作空间，按"时间戳_领域标签"命名（如 `20260721_143022_CS-AI`），每个工作空间独立 SQLite 索引库，互不污染。

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

### 向量检索流程

采用两阶段检索：阈值过滤（默认 0.3）→ bi-encoder 召回 Top-K×4 → cross-encoder 重排得到 Top-K，平衡召回率与精度。

---

## 配置说明

所有配置通过 `.env` 文件管理（参考 [`.env.example`](.env.example)）。前端设置面板修改的配置会自动写入 .env 并热重载，无需重启服务。

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
| `TAVILY_API_KEY` | Tavily AI 高质量搜索 API Key（每月 1000 次免费），获取地址：https://tavily.com — 留空则仅使用免费的 DuckDuckGo 搜索 |

---

## 前端与桌面端

Sage 提供基于 **Vue 3 + Vite** 的 Web 前端，可独立运行或由后端静态托管；同时支持通过 **Electron** 打包为 Windows 桌面应用。

### Web 前端

```bash
cd web
npm install
npm run dev      # 开发模式（http://localhost:5173）
npm run build    # 生产构建到 web/dist/
```

构建产物会被后端 `sage serve` 自动托管在根路径 `/`。

### 设置界面

前端设置面板采用顶部 Tab 切换布局，分为三个区域：

- **模型配置**：Provider 切换、API Key 管理、Base URL、模型列表自动拉取、采样温度、最大 token，以及可选的 Tavily API Key（联网搜索）
- **模型管理**：批量刷新所有 Provider 的可用模型列表，快速切换当前使用的模型
- **版本更新**：检查新版本、查看更新日志、下载并安装新版本

### Electron 桌面端

桌面端通过 `electron-builder` 打包为 Windows NSIS 安装包，主要特性：

- **数据隔离**：用户数据存储在 `%LOCALAPPDATA%/Sage`（可通过 `SAGE_DATA_DIR` 环境变量配置），升级重装不会覆盖用户配置和已安装技能
- **卸载清理**：卸载时弹窗询问是否同时删除本地数据（默认勾选），覆盖范围包括 `%LOCALAPPDATA%/Sage`（后端配置、对话记录、技能）、`%APPDATA%/Sage`（工作空间、论文 PDF、索引数据库）和 `%APPDATA%/sage-paper`（Electron 渲染进程 localStorage，含模型配置缓存）
- **自动启动**：安装完成后静默启动应用
- **自动更新**：内置版本检查，支持 GitHub 直连 / GitHub 镜像 / PyPI 多源回退，下载 URL 自动包装为国内镜像加速
- **安全打包**：`sage.spec` 在 PyInstaller 打包前临时清空 `.env` 并移除开发环境的 `memory.db` / `registry.json`，避免泄露开发数据，打包后自动恢复
- **OCR 完整打包**：`sage.spec` 通过 `collect_data_files` + `collect_dynamic_libs` 收集 `rapidocr_onnxruntime` 的 `config.yaml`、`.onnx` 模型文件和 `onnxruntime` 原生库，确保打包后 OCR 功能可用
- **进程管理**：应用关闭时通过 `taskkill /pid {pid} /f /t` 强制终止后端进程，确保端口释放

详细打包流程参见 [`web/electron/`](web/electron/) 与 [`sage.spec`](sage.spec)。

---

## 版本管理

Sage 采用**单一来源（single source of truth）**版本号管理：项目根目录的 [`VERSION`](VERSION) 文件是唯一权威，所有其他位置自动从它同步。

### 同步拓扑

```
              ┌─────────────────────┐
              │  /VERSION  (0.6.1)  │   ← 唯一权威
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

### 提升版本号

```bash
# 查看当前版本
python scripts/bump_version.py show

# Patch / Minor / Major 升级
python scripts/bump_version.py patch    # 0.6.1 -> 0.6.2
python scripts/bump_version.py minor    # 0.6.1 -> 0.7.0
python scripts/bump_version.py major    # 0.6.1 -> 1.0.0

# 预发布版本
python scripts/bump_version.py pre --tag rc

# 直接设置
python scripts/bump_version.py set 1.2.3
```

`bump_version.py` 会自动更新 `VERSION` 文件、追加 `CHANGELOG.md` 条目，并调用 `sync_version.py` 级联同步到 `web/package.json`、`web/electron/main.cjs` 等位置。

### 前端构建时自动同步

`web/package.json` 已配置 prebuild/prepack/predev 钩子，`npm run build` / `npm run dev` / `npm pack` 会自动调用同步脚本，无需手动干预。

### CI 一致性检查

```yaml
- name: Check version consistency
  run: python scripts/sync_version.py --check
```

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
| `sage collaborate` | 写作模式演示（智能选择流程） |
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

### Sage 工作空间 API（12 个）

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
| GET | `/api/sage/workspaces/{ws_id}/papers/download` | 下载指定论文 |
| DELETE | `/api/sage/workspaces/{ws_id}/papers?path=xxx` | 删除指定论文（带路径遍历防护） |

### Sage 论文工具 API（6 个）

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
| POST | `/chat/stream` | 流式对话（SSE，支持 `mode=single` 单Agent / `mode=writing` 写作模式） |
| GET | `/conversations` | 列出历史对话 |
| POST | `/conversations` | 创建新对话 |
| GET | `/conversations/{id}/messages` | 获取对话消息 |
| DELETE | `/conversations/{id}` | 删除对话 |
| POST | `/index` | 索引当前工作空间 |
| GET | `/api/agent/info` | Agent 信息 |
| GET | `/api/agents` | 列出所有智能体角色 |
| GET | `/api/workspace` | 当前工作空间信息 |
| GET | `/api/workspace/tree` | 工作空间文件树 |
| POST | `/api/workspace` | 创建/写入工作空间文件 |
| GET | `/api/tools` | 列出所有工具 |
| GET | `/api/skills` | 列出已安装技能 |
| GET | `/api/skills/remote-search` | 远程搜索技能 |
| POST | `/api/skills/install` | 安装远程技能 |
| GET | `/api/skills/manifest` | 技能清单 |
| GET | `/api/models` | 可用模型列表 |
| GET/POST | `/api/user-settings` | 用户设置（含 `tavilyApiKey` 可选字段） |
| POST | `/api/model/preload` | 预加载模型 |
| GET | `/api/model/download-progress` | 模型下载进度 |
| GET | `/api/token-stats` | Token 使用统计 |
| GET | `/api/version/check` | 检查版本更新 |
| POST | `/api/version/download` | 下载新版本（SSE 流式进度） |
| POST | `/api/version/install` | 安装新版本 |
| GET | `/memory/stats` | 记忆系统统计 |
| GET | `/memory/memories` | 列出长期记忆 |
| POST | `/memory/search` | 语义检索记忆 |
| GET | `/memory/summaries` | 列出对话摘要 |
| GET | `/health` | 健康检查 |

---

## 开发与测试

### 安装开发依赖

```bash
pip install -e ".[dev,paper]"
```

### 运行测试

```bash
pytest tests/ -v                              # 全部测试
pytest tests/test_workspace_manager.py -v     # 单个测试文件
pytest tests/test_api.py::TestSageWorkspacesAPI::test_upload_file -v  # 指定测试
```

测试套件覆盖配置、记忆、上下文索引、工具、技能系统、智能体、工作空间管理、API、Agent Loop 等模块（共 200+ 测试用例）。

### 测试隔离设计

- 每个测试通过 `tmp_path` + `monkeypatch.chdir` 实现文件系统隔离
- `env_setup` 夹具显式设置所有环境变量，避免 `.env` 污染
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
- [Electron](https://www.electronjs.org/) — 桌面端框架
- [Vue 3](https://vuejs.org/) — 前端框架
