# Sage — Multi-Agent Collaborative Academic Paper Writing Assistant

> An academic writing assistant for high-level journals and conferences such as **SCI / SSCI / CSSCI / EI**. Eight specialized agents collaborate throughout the complete writing workflow — from topic selection, literature review, methodology design, drafting, citation management, to review and verification.

**Current Version: 1.2.5** · Python ≥ 3.11 · Windows / macOS / Linux / Electron Desktop

***

## Table of Contents

- [Core Features](#core-features)

- [Quick Start](#quick-start)

- [System Architecture](#system-architecture)

- [Writing Mode](#writing-mode)

- [Draft Review (One-on-One Writing Workbench)](#draft-review-one-on-one-writing-workbench)

- [Multi-Agent Roles](#multi-agent-roles)

- [Skill Packages](#skill-packages)

- [Toolset](#toolset)

- [Workspace Management](#workspace-management)

- [Configuration](#configuration)

- [Frontend & Desktop](#frontend--desktop)

- [Version Management](#version-management)

- [CLI Commands](#cli-commands)

- [HTTP API](#http-api)

- [Development & Testing](#development--testing)

- [License](#license)

***

## Core Features

- **Unified Conversation Pipeline**: All conversations (no longer distinguishing between single Agent / writing mode entry points) are routed through unified intent analysis — **LLM primary judgment for long inputs (≥30 characters) and rule-based rapid judgment for short inputs**. Simple tasks are routed to the matching role Agent or a general assistant; complex tasks automatically launch the full 8-agent collaboration workflow. The `mode` field is deprecated. High-risk tasks (complex / low-confidence) pass through an **intent confirmation gate** that echoes back the intent for user confirmation or correction before execution, avoiding unintended actions.

- **Multi-Agent Collaboration**: Eight role-specialized agents (Orchestrator / Literature / Methodology / Writer / Citation / Consolidator / Verifier / Reviser) with a collaboration model of "dynamic Orchestrator scheduling + batched parallel execution".

- **Dynamic Execution Plan**: The Orchestrator uses the LLM to generate an execution plan (JSON) based on user requirements, organizes sub-agents into batches executed in parallel, validates dependency correctness with rules, and falls back to the classic serial flow when validation fails.

- **Shared Draft Document (Full-Text Consistency)**: [`PaperProject`](src/sage/paper_project.py) serves as the multi-agent shared draft. The outline (chapter tree + word budget), complete outputs from every role, and section-wise body text are all persisted. Downstream roles can read the full text rather than 2000-character fragments. The final draft is saved to `paper.md`, supporting cross-session loading and multi-round revision.

- **Outline First**: The Orchestrator produces a structured IMRaD outline first; the Writer writes section by section following the outline, with each section starting with a `##` heading, preventing omitted chapters and structural confusion.

- **Deterministic Quality Gate & Second Review**: [`paper_quality.py`](src/sage/paper_quality.py) performs LLM-independent hard validation (chapter completeness, `[CITE:]` residue, reference existence, word budget). When fixable problems are found, it triggers a "review → revise → re-check" closed-loop second review (up to 2 rounds), followed by an LLM soft review after revision.

- **Data Placeholder & Source Suggestions**: During drafting, experimental data / statistical results use `【数据】` placeholders (fabrication forbidden). After the draft is complete, [`paper_data.py`](src/sage/paper_data.py) scans placeholders and the LLM generates "data processing and source suggestions".

- **Multi-Format Export**: [`paper_export.py`](src/sage/paper_export.py) supports LaTeX / Word export, auto-exporting `paper.tex` after the final draft.

- **Pre-Generation Cost Estimation**: `estimate_paper_cost()` estimates the full-scale text volume and LLM call count based on the outline's target word count, reported to the user before batch execution.

- **Long-Task Progress Feedback**: Tools report progress in real time during execution (`index_papers` per file, `ocr_document` per page, `parse_pdf` per stage, `check_plagiarism` per paragraph). The frontend status bar and tool-card progress bars update in sync.

- **LLM Retry Visualization**: LLM retry progress is fed back in real time via SSE events; the status bar shows "Retrying (1/3)...", and the tool area shows an orange retry card (with attempt count, retry reason, delay), fully visible to the user.

- **Thinking Content Output**: Automatically captures the reasoning chain (`reasoning_content`) of reasoning models (such as DeepSeek-R1) into an independent card, collapsed by default, clickable to expand and view the full reasoning process.

- **Transparent Token Consumption**: The tool / agent / skill call cards show the total tokens consumed in that round in real time, for cost monitoring.

- **Context Usage Indicator**: A ring indicator built into the chat input box next to the selected model displays in real time the current context occupancy ratio, maximum context window, and the compression threshold tick mark (at 80%). A hover tooltip shows current occupancy / model maximum window, compression trigger threshold, number of compressed rounds, and cumulative saved tokens. The compression threshold is automatically calculated dynamically per model (model window × 80%). After switching models, if the occupied context exceeds the new model's window, it auto-compresses with a prompt during compression; each conversation's context occupancy and compression stats are recorded independently (`compressed_rounds` / `saved_tokens` persisted to SQLite, cleared only when that conversation is deleted).

- **Draft Review View**: After a draft is complete in writing mode, it enters an independent review workbench supporting chapter-tree navigation, word-count progress tracking (chapter target / current), AI-pattern detection, LLM deep rewriting, `【数据】` placeholder backfill (inline input then **directly replace that placeholder**, pure string replacement without LLM), chapter locking, targeted revision, Word export, and full-text reference authenticity verification. A snapshot is auto-saved before each revision / rewrite / data backfill, allowing rollback anytime. Papers generated by different conversations are isolated from each other (stored per conversation ID in `.sage/papers/{conversationID}/`); the review list page shows each draft's topic, word-count progress, and update time, allowing return and switching at any time.

- **Reply Layout Self-Healing**: Some LLMs output newline-per-word (vertical) or break markdown tables into fragments; the backend normalizes before persisting messages (merging vertical fragments, restoring scattered tables to standard tables), and provides a one-click `/api/review/fix-vertical-messages` for legacy history messages.

- **Revision Retention Rate Check**: In the multi-round revision loop, if LLM output shrinks significantly (word count < 70% of the pre-revision original), it's judged as truncation / omission and refuses to overwrite the old draft, giving a warning rather than silently losing content.

- **Reliability Hardening (from multi-round code review fixes)**: Context compression uses rolling summaries (historical summary merged into the new one, preserving early decisions); **parallel execution of read-only tools** (`read_file` / `list_dir` / `search_literature` / web search — when ≥2 tools in the same batch are all read-only, they run concurrently, wall-clock time dropping from "sum" to "max"); fine-grained cache invalidation for tool deduplication (write-type tools invalidate related entries by path, `list_dir` fully, unrelated read-only results preserved, preventing stale content); parallel batches use single-queue FIFO true streaming forwarding with automatic cancellation of remaining tasks on exception; blocking operations (search, version install, session memory save) are all moved off the event loop; SQLite singleton with write lock + WAL concurrency safety; tool execution timeouts by type (search 30s / parse 120s, returning errors on timeout for the LLM to switch paths); web fetch with SSRF protection and 2MB limited reads; LLM error classification boundary matching (status codes no longer falsely match concatenated numbers); circuit breaker shared by LLM base_url (single trip for the same provider protects all users with rate limiting).

- **Session Concurrency Protection**: Only one SSE request is allowed per session at a time; conflicts return a `busy` event with a "previous message still processing" prompt. Agent cache uses LRU eviction and skips running sessions to avoid accidental deletion.

- **Atomic Config Writes**: `.env` uses a temp file + `os.replace()` atomic replacement + write lock; API keys are masked in logs; write failures explicitly return 500 rather than silently failing.

- **Local Literature Index (Optional)**: Builds a vectorized index based on `sentence-transformers` (all-MiniLM-L6-v2, 384-dim) for semantic retrieval over the uploaded literature library. `sentence-transformers` is moved out of core dependencies into the `embed` optional dependency. Chat functionality is unaffected when not installed; only vector-index-related features prompt for installation.

- **Multi-Document Parsing**: Supports PDF / Word / LaTeX / scanned OCR, auto-extracting metadata such as title, authors, year, DOI, abstract, keywords, and verifying key fields like journal names through external sources (Weipu / Wanfang / CrossRef).

- **External Academic Search**: Integrates four academic data sources — Google Scholar / arXiv / CrossRef / Semantic Scholar — for supplementary search and citation authenticity verification. Additionally integrates the `search_cnki` tool, which authenticates paper metadata via Weipu / Wanfang web search + CrossRef API fallback (e.g., correcting common journal-name/column-name confusion in PDF extraction).

- **Web Search (Optional)**: Integrates Tavily AI search (`web_search_pro`), auto-switching when DuckDuckGo free search results are low quality. Requires `TAVILY_API_KEY` (1000 free requests per month).

- **Multi-Format Citation Management**: Supports six citation formats — APA / MLA / GB-T7714 / Vancouver / Chicago / IEEE — with automatic insertion and formatting.

- **AI-Pattern Reduction**: `reduce_ai_pattern` uses a rule library to detect typical AI traces (clichés / repetitive sentence patterns / conjunction stacking / absolute assertions / vague abstract words / mechanical parallelism); `rewrite_deai` performs LLM deep rewriting based on detection results (sentence diversification, terminology concretization, preserving original meaning and citations) to evade AI detection.

- **Multi-Workspace Management**: Named by "timestamp_domain_tag" (e.g., `20260721_143022_CS-AI`). Each workspace has an independent SQLite index database, preventing cross-contamination.

- **Reflection & Self-Correction**: Tools auto-reflect after execution and auto-correct on failure (heuristic rules + retry cap + circuit-breaker protection).

- **Three-Tier Memory System**: Working memory (conversation context) + long-term memory (lessons learned) + semantic memory (vector retrieval).

- **Provider Switching**: Connects DeepSeek / Qwen / OpenAI / Anthropic / Zhipu / Moonshot etc. via the OpenAI-compatible protocol, with visual switching in the frontend settings screen — no code changes required.

- **Desktop Distribution**: Packaged as a Windows installer via Electron + NSIS, supporting automatic update checks, mirror-accelerated downloads, and auto-start after silent install.

***

## Quick Start

### Installation

```bash
# Clone the project
git clone <repo-url>
cd Sage

# Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# Install core dependencies
pip install -e .

# (Optional) Install paper/document parsing dependencies
pip install -e ".[paper]"

# (Optional) Install local vector index (literature index/semantic retrieval/semantic memory, includes sentence-transformers)
pip install -e ".[embed]"

# (Optional) Install development and test dependencies
pip install -e ".[dev]"
```

### First-Time Configuration

```bash
# Copy the config template
cp .env.example .env

# Edit .env, at least fill in LLM_CHAT_API_KEY
# DeepSeek recommended (cost-effective): https://platform.deepseek.com/api_keys
```

Or use the interactive configuration wizard:

```bash
sage init
```

You can also configure after starting the service in the frontend settings screen (supports multi-Provider switching, API Key management, automatic model list fetching).

### Start the Service

```bash
# Start the API service (default http://127.0.0.1:8000)
sage serve

# Or enter interactive chat
sage chat
```

### Create Your First Paper Workspace

```bash
# Create a workspace via the API
curl -X POST http://127.0.0.1:8000/api/sage/workspaces \
  -H "Content-Type: application/json" \
  -d '{"domain_tag": "CS-AI", "description": "AI direction paper", "index_level": "SCI"}'

# Upload papers
curl -X POST http://127.0.0.1:8000/api/sage/workspaces/<ws_id>/upload \
  -F "file=@paper.pdf" \
  -F "subdir=papers"

# Trigger vectorized indexing (auto-triggered after upload; this is a manual rebuild)
curl -X POST "http://127.0.0.1:8000/api/sage/workspaces/<ws_id>/index?force=true"

# Start conversational writing
sage chat "Help me write a research background about the Transformer attention mechanism based on the uploaded literature"
```

***

## System Architecture

Sage uses a 6-layer architecture, bottom-up:

| Layer | Module | Responsibility |
| --- | --- | --- |
| **1. Foundation Model Layer** | `llm/client.py` | Connects LLMs via the OpenAI-compatible protocol, supporting function calling and streaming output |
| **2. Development Framework Layer** | `agent/loop.py`, `agent/system_prompt.py` | Custom Agentic Loop (think → call tool → observe result → continue thinking) |
| **3. Memory & Context Layer** | `memory/`, `context/` | SQLite persistence + vector Embedding + conversation history compression |
| **4. Tools & Integration Layer** | `tools/`, `skill_system.py` | 18+ Sage-specific tools + skill system + SkillHub remote skill downloads |
| **5. Multi-Agent Collaboration Layer** | `agents/`, `paper_project.py`, `paper_quality.py`, `paper_data.py`, `paper_export.py`, `citation_verify.py` | 8 role agents + Orchestrator dynamic scheduling + batched parallel execution + shared draft document + deterministic quality gate + second review + data placeholder + reference authenticity hard check + multi-format export |
| **6. Operations & Governance Layer** | `core/observability.py`, `core/resilience.py`, `core/mcp.py`, `core/crossref.py` | Observability + resilient retry + circuit breaker + MCP protocol support + CrossRef shared client |

### Collaboration Flow

Writing mode uses a "intent analysis → intelligent routing" layered architecture. Simple tasks are handled directly by the matching role Agent; complex tasks are dynamically planned by the Orchestrator and executed by scheduling sub-agents in batches in parallel:

```
User writing request
     │
     ▼
┌──────────────────────────────────────────┐
│  Intent Analysis (Orchestrator ·         │
│  Unified Conversation Pipeline)          │
│  1. Clarification loop: ask back first   │
│     when information is severely lacking │
│  2. Long input(≥30 chars) → LLM primary  │
│     Short input(<30 chars) → rule rapid  │
│     Output {complexity, role, reason,    │
│            confidence:high/medium/low}   │
│  3. Confirmation gate: complex OR low    │
│     confidence → pause, echo intent for  │
│     confirm/correct (correction feeds     │
│     back into memory)                    │
└──────────────────┬───────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
      Simple task             Complex task
        │                     │
        ▼                     ▼
┌─────────────────┐  ┌─────────────────────────────────────────────┐
│ Matching role   │  │  Step 1: Orchestrator generates execution   │
│ Agent           │  │  plan (LLM, JSON)                           │
│ ┌─────────────┐ │  │  ┌───────────────────────────────────────┐  │
│ │literature   │ │  │  │ {"batches": [                         │  │
│ │planner      │ │  │  │   {"id":1,"roles":["literature",      │  │
│ │coder        │ │  │  │     "planner"],"depends_on":[]},      │  │
│ │reviewer     │ │  │  │   {"id":2,"roles":["coder"],          │  │
│ │debugger     │ │  │  │     "depends_on":[1]},                │  │
│ │citation     │ │  │  │   {"id":3,"roles":["consolidator"],   │  │
│ │consolidator │ │  │  │     "depends_on":[2]},                │  │
│ │general      │ │  │  │   {"id":4,"roles":["citation"],       │  │
│ └─────────────┘ │  │  │     "depends_on":[3]},                │  │
│ or general      │  │  │   {"id":5,"roles":["reviewer"],       │  │
│ assistant       │  │  │     "depends_on":[3,4]}               │  │
│ fallback        │  │  │ ]}                                    │  │
└─────────────────┘  │  └───────────────────────────────────────┘  │
                     │  Step 2: Rule-based dependency validation    │
                     │  (falls back to classic serial flow on fail)│
                     └──────────────────┬──────────────────────────┘
                                        │
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  Step 3: Outline first + cost estimation   │
                     │  Orchestrator generates IMRaD structured   │
                     │  outline (chapters + word budget)          │
                     │  estimate_paper_cost() reports scale &     │
                     │  call counts                               │
                     └──────────────────┬─────────────────────────┘
                                        │
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  Step 4: Execute batches in parallel       │
                     │  (write to PaperProject shared draft,      │
                     │  downstream reads full text not fragments) │
                     │                                            │
                     │  Batch1: ┌──────────┐ ┌──────────┐         │
                     │          │Literature│ │Methodol- │ parallel│
                     │          │          │ │ogy       │         │
                     │          └──────────┘ └──────────┘         │
                     │  Batch2: ┌──────────┐                      │
                     │          │ Writer   │ write per outline    │
                     │          └──────────┘                      │
                     │  Batch3: ┌──────────────┐                  │
                     │          │Consolidator  │ integrate outputs│
                     │          └──────────────┘                  │
                     │  Batch4: ┌──────────────┐                  │
                     │          │ Citation     │ cite/format/check│
                     │          └──────────────┘                  │
                     │  Batch5: ┌──────────────┐                  │
                     │          │ Reviewer     │ multi-verification│
                     │          └──────────────┘                  │
                     └──────────────────┬─────────────────────────┘
                                        │
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  Step 5: Deterministic quality gate        │
                     │  (paper_quality.py)                        │
                     │  chapter completeness / [CITE:] residue /  │
                     │  reference existence / word budget         │
                     │  (pure rules, no LLM)                      │
                     └──────────────────┬─────────────────────────┘
                                        │
                          ┌─────────────┴─────────────┐
                          │  Fixable problems?        │
                          └─────────────┬─────────────┘
                      Yes ──────────────┘ └─────────────┘ No
                     ▼                                      ▼
                     ┌──────────────────────┐  ┌──────────────────┐
                     │ Review→Revise second │  │ Go to next step  │
                     │ review (max 2 rounds):│  └────────┬─────────┘
                     │ Reviser targeted     │           │
                     │ coverage (only      │           │
                     │ outputs modified     │           │
                     │ chapters) + rejects  │           │
                     │ invalid output; after│           │
                     │ revision run LLM     │           │
                     │ soft review          │           │
                     └──────────┬───────────┘           │
                                └──────────┬────────────┘
                                           ▼
                     ┌────────────────────────────────────────────┐
                     │  Step 6: Reference authenticity hard check │
                     │  (optional, non-blocking)                 │
                     │  verify_references CrossRef/Weipu/Wanfang  │
                     │  (deterministic, no LLM), report persisted│
                     │  to snapshot                               │
                     └──────────────────┬─────────────────────────┘
                                        │
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  Step 7: Data placeholder scan + finalize  │
                     │  paper_data.py scans 【数据】 and generates │
                     │  source suggestions                       │
                     │  PaperProject.finalize() saves paper.md    │
                     └──────────────────┬─────────────────────────┘
                                        │
                                        ▼
                     ┌────────────────────────────────────────────┐
                     │  Step 8: Multi-format export               │
                     │  paper_export.py auto-exports paper.tex    │
                     │  Supports LaTeX / Word export              │
                     └──────────────────┬─────────────────────────┘
                                        │
        ┌──────────┬───────────────────┘
        │          │
        ▼          ▼
     Final paper output (paper.md + paper.tex + citations.md reference checklist)
```

**Key Design**:

- Sub-agents in the same batch are executed in parallel via [`_run_parallel_workers`](src/sage/agents/orchestrator.py). Each worker's events are written to a shared `asyncio.Queue` and forwarded by the main loop in FIFO order in real time (true streaming interleaved output, no event loss). If any worker raises an exception, the remaining tasks are automatically canceled.

- Later batches can depend on the outputs of earlier batches (context passed through cumulative `batch_results` dictionary).

- Before plan validation, batch IDs are normalized (string / number unified to int), then rule validation checks dependency correctness (citation depends on coder/consolidator, reviewer depends on coder/citation, etc.), avoiding false fallbacks due to type mismatch.

- Orchestration LLM calls (intent analysis / plan generation / outline generation) uniformly include retry; on failure they still fall back to their respective fallbacks (classic pipeline / default outline / general assistant).

- Shared draft [`PaperProject`](src/sage/paper_project.py): each role's full output is written to the draft; downstream reads the full text (safety-valve truncation only above a 45000-token budget), solving the "previous output left only a 2000-character fragment" full-text consistency bottleneck. Each worker's output is written and persisted as it is produced, making in-progress versions visible in real time in the review view.

- Deterministic quality gate & second review: [`paper_quality.py`](src/sage/paper_quality.py) hard validation triggers the "review → revise → re-check" loop (up to 2 rounds). Revision uses **targeted coverage** (only outputs the modified chapters; unchanged chapters keep their original text, no whole-document rebuild to save tokens), validates output completeness by checking for chapter headings, and runs an LLM soft review after revision.

- Reference authenticity hard check: [`citation_verify.py`](src/sage/citation_verify.py) hard-verifies every `[CITE:]` in the text and the end-of-document entries against CrossRef / Weipu / Wanfang (deterministic, no LLM). Network failures only mark but don't block draft completion. The Citation agent's output is written to `citations.md`, a reference checklist for per-entry review.

- Multi-round revision routing: revision-type instructions ("make the conclusion more conservative") read the existing draft, modify, and write back; new-paper tasks clear the old draft; checkpoint continuation ("continue writing") continues unfinished chapters based on the existing draft.

- LLM retry is passed through via SSE `retry` events, fully visible to the user throughout the process.

***

## Writing Mode

Writing mode is Sage's core working mode, using a **intent analysis → intelligent routing** unified layered architecture: all conversations (both regular dialogue and paper writing use the same entry point) first analyze intent. Simple tasks are routed to a single role Agent; complex tasks automatically switch to the multi-agent collaboration workflow, balancing efficiency and quality.

### Unified Conversation Pipeline & Intent Analysis

All conversations uniformly go through [`_collaborate_stream`](src/sage/api.py) (`/chat/stream`), which first analyzes intent then routes (the `mode` field is deprecated). Analysis is implemented by [`AgentOrchestrator._analyze_intent()`](src/sage/agents/orchestrator.py), selecting a strategy by input length and producing **intent confidence**:

| Strategy | Applicable | Description |
| --- | --- | --- |
| **Rule-based rapid judgment** `_quick_classify` | Short inputs (<30 characters) preferred | Millisecond-level rule matching; hit yields `confidence=high` |
| **LLM fine-grained analysis** `_analyze_intent_with_llm` | Long inputs (≥30 characters) primary | Long inputs are prone to rule false positives, so delegated to LLM; rules as fallback |
| **Degradation fallback** | Any strategy fails | Degrades to complex task, `confidence=low` (triggers confirmation gate) |

**Confidence** (`IntentResult.confidence`): `high` (rule hit) / `medium` (LLM judging) / `low` (degraded fallback). Low confidence and complex tasks → trigger the [intent confirmation gate](#intent-confirmation-gate).

#### Rule-Based Rapid Judgment (`_quick_classify`)

Matches in priority order:

| Priority | Judgment rule | Routing result |
| --- | --- | --- |
| 1 | Revision intent (modify/polish + paper/content/paragraph etc.) | Simple task → Reviser `debugger` |
| 2 | Local paper writing (paper's abstract/TOC/chapter N etc.) | Simple task → Writer `coder` |
| 3 | Full-paper writing keywords (complete paper/multi-chapter/SCI/SSCI/big thesis etc.) | Complex task → multi-agent collaboration |
| 4 | Verb+object pattern fine matching (`_match_role_by_patterns`) | Simple task → matching role Agent |
| 5 | Greetings/simple dialogue (hello/thanks etc.) | Simple task → general assistant |
| 6 | Short question (<30 characters) fallback matching (keyword hits a specific role) | Simple task → matching role Agent |
| — | Other uncertain cases | Delegated to LLM analysis |

**Verb+object pattern matching** (`_match_role_by_patterns`) allocates agents with the following priorities, avoiding prefix interference (e.g., "literature review") false-matching the real writing intent:

| Priority | Matching pattern | Routed role |
| --- | --- | --- |
| 1 | Writing instructions (generate/write/compose + TOC/abstract/chapter/outline etc.) | `coder` (Writer) |
| 2 | Literature search (search/find/review + literature/materials/related research) | `literature` (Literature) |
| 3 | Citation handling (format/normalize + citation format/reference format) | `citation` (Citation) |
| 4 | Review tasks (review/verify/check + logic/standards/quality) | `reviewer` (Verifier) |
| 5 | Revision tasks (modify/revise/polish + paper/content/paragraph) | `debugger` (Reviser) |

#### LLM Fine-Grained Analysis (`_analyze_intent_with_llm`)

Calls the LLM (no tools, `max_tokens=200`) to output structured JSON, validating `complexity` / `role` by rules (complex forces `role=supervisor`):

```json
{
  "complexity": "simple",   // simple | complex
  "role": "literature",      // best-matching role for simple; supervisor for complex
  "reason": "query literature related research"  // judging reason (shown to user)
}
```

This path recalls past [intent corrections](#intent-confirmation-gate) (`intent_correction`) from semantic memory to hint the LLM to avoid repeated misjudgment. On analysis failure it degrades to a complex task (multi-agent fallback to ensure nothing is missed). LLM distinction: long inputs may be falsely matched by rules, so when `len>=30` the LLM is the primary judge.

#### Clarification Loop

When information is severely lacking (extremely short, or bare requests like "write me a paper" with no topic), ask back to clarify rather than blindly start.

### Routing Results

| Task type | Handling | Example |
| --- | --- | --- |
| **Simple task** | Handled by the matching role Agent chosen by intent analysis, with the general assistant as fallback when no match | "search Transformer-related literature for me" → literature |
| **Complex task** | The Orchestrator uses the LLM to generate a dynamic execution plan, schedules sub-agents in batches in parallel, and finally runs quality checks | "write a complete paper about attention mechanisms" |

> **Confirmation gate trigger**: Any task judged as complex, or a high-risk task whose intent analysis fell back to `low` confidence, must pass through the [intent confirmation gate](#intent-confirmation-gate) for user confirmation/correction before actually executing.

### Frontend Interaction

All conversations are routed through intent analysis uniformly (no manual writing-mode switching):

- The Orchestrator first analyzes intent; the process is shown via `reflection` events: `Intent analysis result: simple task → literature agent`

- Simple-task replies carry a role prefix: `**[Literature Agent]** content`

- Complex tasks have the Orchestrator generate an execution plan, shown via `reflection` events (e.g., "Batch 1: Literature + Methodology (in parallel)"); each sub-agent's progress is shown in real time via `collaborate` events

- LLM retries are fed back in real time via `retry` events; the status bar shows `[Role] retrying (1/3), retry in 2.0s...`, and the tool area shows an orange retry card

### Intent Confirmation Gate

For **high-risk tasks** (complex tasks, or `low` confidence — i.e., the degraded fallback after LLM intent analysis failure), it pauses before execution and pops up the [`IntentConfirm`](web/src/components/IntentConfirm.vue) dialog. **No task executes before user action**:

- The dialog echoes the Orchestrator's **intent conclusion** (task complexity / matched role / confidence / judging reason). The user can:

  - **Confirm and continue** — execute per the current intent
  - **Correct** — modify 「role / complexity」 and add remarks, triggering re-analysis (forming a correction loop)
  - **Cancel** — abandon this task

- The gate is triggered via the `intent_confirm_required` SSE event; it is skipped after confirmation or when `force_role` / `force_complexity` / `auto_confirm` are explicitly specified, avoiding an infinite loop after confirmation

- **Intent correction feedback**: corrections made at the gate are recorded as `intent_correction` (written to DB + semantic memory, forming a user profile), recalled by later intent analysis so the system "remembers" your preferences — the more you use it, the more accurate it becomes (zero extra LLM calls)

### Thinking Content & Token Display

- **Thinking content**: automatically captures the `reasoning_content` field of reasoning models (DeepSeek-R1 etc.), passed to the frontend via the `reasoning` SSE event, displayed as a purple independent card, collapsed by default, clickable to expand and view the full reasoning process

- **Token consumption**: each round's LLM token usage is passed via the `tokens` field, displayed next to the tool / agent / skill call card titles as total tokens consumed

### LLM Retry Visualization

The entire LLM retry process is transparent to the user; event chain:

```
AgentLoop._call_llm_stream_with_retry (generates retry event)
    │
    ▼
Orchestrator._map_event (passes through as CollaborationEvent(type='retry'))
    │
    ▼
api.py _collaborate_stream / chat_stream (serialized as SSE retry event)
    │
    ▼
useChat.js (updates status bar + pushes retry card)
    │
    ▼
ToolCall.vue (renders orange llm_retry card: ↻ icon + attempt count + error reason)
```

The `retry` event includes fields: `attempt` (current attempt), `max_retries` (max retries), `delay` (retry delay seconds), `error` (error message, first 200 chars), `role` (role name, optional).

***

## Draft Review (One-on-One Writing Workbench)

After a draft is complete, the "Draft Review" view allows chapter-by-chapter fine-processing of the paper, supporting a complete review loop for one draft:

### Entry & Multi-Conversation Isolation

- The sidebar 「Draft Review」 enters the **draft list page**: shows all conversations with generated papers (conversation title, paper topic, word-count progress, update time). Each draft is isolated by conversation ID in `.sage/papers/{conversationID}/`, so they don't overwrite each other

- Click to enter the **review page**: navigate by chapter tree, with total word-count progress at the top (written / target); the back button at the top returns to the list page, where you can switch to another conversation's paper at any time

- Deleting a conversation also cleans up its corresponding draft directory, leaving no residue

### Review Operations

| Operation | Description |
| --- | --- |
| AI pattern detection | Rule library scans chapters, marking positions and reasons of suspected AI-generated traces |
| Deep rewrite | After manual confirmation of detection results, calls the LLM to reduce AI patterns, preserving original meaning and citations |
| Data backfill | Scans `【数据】` placeholders; inline data input then **directly replaces that placeholder** (pure string replacement, no LLM) |
| Chapter locking | Locks specified chapters so later AI modifications don't touch them (protect finalized content) |
| Targeted revision | Instruction-based revision of specified chapters; if the full text shrinks more than 30%, judged as truncation, refusing to overwrite and warning |
| Version history | Auto-saves snapshots before revision / rewrite / data backfill, viewable and one-click rollback |
| Word export | Exports the current draft to `.docx` in outline order |
| Reference verification | Hard-verifies every `[CITE:]` marker and end-of-document entry against CrossRef/Weipu/Wanfang, generating an authenticity report |
| Layout repair | Fixes vertical / broken layout and scattered tables in history messages (`fix-vertical-messages`) |

All review operations carry `conversation_id`, ensuring they only act on the current conversation's draft.

***

## Multi-Agent Roles

Eight agents are defined under [`src/sage/agents/`](src/sage/agents/), each with its own `agent.json` and optional dedicated skills. In writing mode, a general assistant (not bound to a role prompt) is also dynamically used to handle simple tasks with no matching role:

| Role | English name | Directory | Responsibility |
| --- | --- | --- | --- |
| 主编 (Orchestrator) | Orchestrator | `supervisor/` | Task decomposition, sub-agent scheduling, flow control, quality gate |
| 文献调研员 (Literature) | Literature | `literature/` | Literature search, review, research-status analysis |
| 方法论专家 (Methodology) | Methodology | `planner/` | Research method design, experimental schemes, argumentation frameworks |
| 撰写员 (Writer) | Writer | `coder/` | Concrete writing of paper chapters |
| 引用管理员 (Citation) | Citation | `citation/` | Citation insertion, reference formatting, plagiarism detection |
| 整理汇报员 (Consolidator) | Consolidator | `consolidator/` | Integrates outputs from sub-agent discussions |
| 审校核查员 (Verifier) | Verifier | `reviewer/` | Multi-faceted verification (literature library + logic + external search + academic standards) |
| 修订员 (Reviser) | Reviser | `debugger/` | Fixes problems per the review report |
| 通用助手 (General) | General | — | Fallback agent for no-matching-role simple tasks in writing mode, handling Q&A/explanation/general tasks |

Agent definitions are loaded via [`sage.agents.loader.AgentLoader`](src/sage/agents/loader.py); orchestration logic is in [`sage.agents.orchestrator.AgentOrchestrator`](src/sage/agents/orchestrator.py). Complex tasks have the Orchestrator generate a dynamic execution plan via [`_generate_execution_plan`](src/sage/agents/orchestrator.py), validate it via [`_validate_plan`](src/sage/agents/orchestrator.py), then execute batches in parallel via [`_run_parallel_workers`](src/sage/agents/orchestrator.py). Intent analysis and intelligent routing are dispatched uniformly by [`_analyze_intent`](src/sage/agents/orchestrator.py) (see the [Writing Mode](#writing-mode) section).

***

## Skill Packages

Five Sage-specific skill packages reside in [`.agent/skills/`](.agent/skills/); each skill contains `skill.json` metadata and trigger conditions:

| Skill package | Description | Tools |
| --- | --- | --- |
| **paper-processing** | PDF/Word/LaTeX/scanned document parsing, metadata extraction, OCR | `parse_pdf`, `parse_docx`, `parse_latex`, `extract_metadata`, `ocr_document` |
| **literature-index** | Literature vector indexing, semantic retrieval, citation management, plagiarism detection | `index_papers`, `search_literature`, `extract_references`, `insert_citation`, `format_references`, `check_plagiarism` |
| **writing-assistant** | Outline generation, paragraph writing, academic polishing, logic checking | `generate_outline`, `write_paragraph`, `polish_academic`, `check_logic` |
| **external-search** | Google Scholar / arXiv / CrossRef / Semantic Scholar external academic search, with CNKI metadata authentication | `search_scholar`, `search_arxiv`, `search_crossref`, `search_semantic_scholar`, `search_cnki` |
| **ai-pattern-reducer** | AI-pattern reduction rewriting to evade AI detection | `reduce_ai_pattern`, `rewrite_deai` |

Skills are loaded via [`sage.skill_system.SkillLoader`](src/sage/skill_system.py); remote skill search/download via the built-in [`sage.skill_hub_client.SkillHubClient`](src/sage/skill_hub_client.py) (no external CLI dependency).

***

## Toolset

All tools are dispatched uniformly through [`sage.tools.engine.ToolEngine`](src/sage/tools/engine.py), following the OpenAI function calling schema:

### General File Operations ([`tools/file_ops.py`](src/sage/tools/file_ops.py))

- `read_file` — read file content (supports line-number ranges)

- `write_file` — create or overwrite a file

- `edit_file` — search-replace precise editing

- `list_dir` — list directory contents

### Sage-Specific Paper Tools ([`tools/paper_ops.py`](src/sage/tools/paper_ops.py))

- `index_papers` — build a vector index over workspace papers

- `search_literature` — semantically search the literature library

- `extract_references` — extract reference lists

- `insert_citation` — insert a citation at a specified position

- `format_references` — format references per the target journal

- `check_plagiarism` — duplicate comparison against the local literature library (only uploaded literature, offline; not a whole-internet check)

- `parse_pdf` / `parse_docx` / `parse_latex` — document parsing (`parse_pdf` auto-authenticates metadata via Weipu / Wanfang / CrossRef)

- `extract_metadata` — extract paper metadata

- `ocr_document` — OCR recognition

- `generate_outline` / `write_paragraph` / `polish_academic` / `check_logic` — writing assistance

- `reduce_ai_pattern` / `rewrite_deai` — AI-pattern detection (rule library) and deep rewriting (LLM, can be based on detection results)

- `search_scholar` / `search_arxiv` / `search_crossref` / `search_crossref_by_query` / `search_semantic_scholar` — external academic search (`search_crossref` verifies a single entry by DOI; `search_crossref_by_query` keyword-searches candidate lists)

- `search_cnki` — authenticates paper metadata via Weipu / Wanfang web search + CrossRef API fallback (journal name, column, ISSN, etc.)

> CrossRef requests uniformly go through the shared client in [`core/crossref.py`](src/sage/core/crossref.py): the User-Agent carries a mailto to enter the polite pool (`SAGE_CROSSREF_MAILTO`), with global rate limiting (default 0.2s interval) + 429/5xx exponential backoff, improving stability and avoiding rate limits during batch verification.

### General Skills & Network ([`tools/skill_ops.py`](src/sage/tools/skill_ops.py), [`tools/web.py`](src/sage/tools/web.py))

- `list_skills` / `load_skill` / `install_skill` — skill management

- `web_search` — DuckDuckGo search (free, no config; used preferentially; thread-pool execution + 25s timeout so it doesn't block the event loop)

- `web_search_pro` — Tavily AI high-quality search (used when `web_search` results are low quality; requires `TAVILY_API_KEY`)

- `web_fetch` — fetch the body of a specified URL (with SSRF protection: refuses targets resolving to private/loopback/reserved ranges; streaming limited to 2MB)

Some tools have execution timeouts at the engine layer (search 30s, parse/writing 120s, others 60s). Timeouts return clear errors so the LLM can switch paths, avoiding a single tool hanging the whole loop.

Tool return values are uniformly the [`ToolResult`](src/sage/tools/types.py) dataclass, with `success` / `output` / `data` / `error` / `metadata` fields.

***

## Workspace Management

Sage supports multiple workspaces, named by "timestamp_domain_tag" (e.g., `20260721_143022_CS-AI`). Each workspace has an independent SQLite index database, preventing cross-contamination.

### Domain Tag Specification

- Only letters / digits / hyphens / underscores are allowed

- Length 2-32 characters

- Recommended format: `<discipline>-<subdirection>`, e.g., `CS-AI`, `MED-Cardio`, `SSCI-PSY`

### Index Levels

Supports four index levels — `SCI` / `SSCI` / `CSSCI` / `EI` — affecting default citation formats and quality-check strictness.

### Isolation Design

- Each workspace uses an independent SQLite database (`.sage/index.db`)

- Isolated via [`WorkspaceStore`](src/sage/workspace_manager.py), **not polluting the global** **`MemoryStore`** **singleton**

- Workspace switching updates `cfg.workspace` via `switch_to()`, without modifying original API interfaces

### Vector Retrieval Flow

Uses two-stage retrieval: threshold filtering (default 0.3) → bi-encoder recall of Top-K×4 → cross-encoder re-ranking to Top-K, balancing recall and precision.

***

## Configuration

All configuration is managed via the `.env` file (see [`.env.example`](.env.example)). Config changes made in the frontend settings panel are auto-written to .env and hot-reloaded without restarting the service.

### Chat LLM Configuration

| Environment variable | Description | Default |
| --- | --- | --- |
| `LLM_CHAT_API_KEY` | LLM provider API Key (required) | — |
| `LLM_CHAT_BASE_URL` | API Base URL | `https://api.deepseek.com` |
| `LLM_CHAT_MODEL` | Model name | `deepseek-chat` |
| `LLM_CHAT_TEMPERATURE` | Sampling temperature | `0.3` |
| `LLM_CHAT_MAX_TOKENS` | Max tokens per generation | `8192` |
| `LLM_CHAT_TIMEOUT` | Request timeout (seconds) | `120.0` |
| `LLM_CHAT_STREAMING` | Whether to stream | `true` |
| `LLM_CHAT_MAX_TOOL_ROUNDS` | Max tool calls per conversation round | `20` |

### Embedding Configuration (Optional, requires the `embed` extra)

> `sentence-transformers` is moved out of core dependencies. When not installed, chat functionality is unaffected; only literature index, semantic retrieval, and semantic memory degrade to unavailable. Install: `pip install 'sage-paper[embed]'`

| Environment variable | Description | Default |
| --- | --- | --- |
| `LLM_EMBEDDING_MODEL` | Local Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| `HF_ENDPOINT` | HuggingFace mirror (recommended in CN) | `https://hf-mirror.com` |

### Memory & Workspace

| Environment variable | Description | Default |
| --- | --- | --- |
| `MEMORY_SQLITE_PATH` | Global memory database path | `data/memory.db` |
| `DEV_AGENT_WORKSPACE` | Default workspace (empty uses current directory) | `.` |
| `DEV_AGENT_MAX_CONTEXT_TOKENS` | Context window token cap (fallback when the model mapping table isn't hit) | `60000` |
| `DEV_AGENT_SUMMARY_TRIGGER_TOKENS` | Summary compression trigger threshold (dynamic mode computes per model window × 80%; this is the fallback when the mapping table isn't hit) | `45000` |

### Optional External Search

| Environment variable | Description |
| --- | --- |
| `TAVILY_API_KEY` | Tavily AI high-quality search API Key (1000 free requests per month), get it at: <https://tavily.com> — leave empty to use only free DuckDuckGo search |
| `SAGE_CROSSREF_MAILTO` | CrossRef polite-pool contact email. Carrying it enters CrossRef's polite pool, making requests faster, more stable, and less rate-limited. Default placeholder `sage@example.com`; replace with a real reachable email for production |

***

## Frontend & Desktop

Sage provides a **Vue 3 + Vite** web frontend that can run standalone or be statically hosted by the backend; it also supports being packaged into a Windows desktop app via **Electron**.

### Web Frontend

```bash
cd web
npm install
npm run dev      # dev mode (http://localhost:5173)
npm run build    # production build to web/dist/
```

The build output is auto-hosted by the backend `sage serve` at the root path `/`.

### Settings Screen

The frontend settings panel uses a top-tab layout split into three areas:

- **Model Configuration**: Provider switching, API Key management, Base URL, automatic model list fetching, sampling temperature, max tokens, and optional Tavily API Key (web search)

- **Model Management**: batch-refresh all providers' available models, quickly switch the currently used model

- **Version Update**: check for new versions, view changelog, download and install new versions

### Electron Desktop

The desktop app is packaged into a Windows NSIS installer via `electron-builder`, with key features:

- **Data Isolation**: user data is stored in `%LOCALAPPDATA%/Sage` (configurable via the `SAGE_DATA_DIR` environment variable); upgrades/reinstalls don't overwrite user config or installed skills

- **Uninstall Cleanup**: on uninstall, a dialog asks whether to also delete local data (checked by default), covering `%LOCALAPPDATA%/Sage` (backend config, conversations, skills), `%APPDATA%/Sage` (workspaces, paper PDFs, index databases), and `%APPDATA%/sage-paper` (Electron renderer-process localStorage, including model-config cache)

- **Auto-start**: silently launches the app after installation

- **Auto-update**: built-in version check supporting multi-source fallback (GitHub direct / GitHub mirror / PyPI); download URLs are auto-wrapped for domestic mirror acceleration

- **Secure Packaging**: `sage.spec` temporarily clears `.env` and removes the dev-environment `memory.db` / `registry.json` before PyInstaller packaging (avoiding dev-data leakage), auto-restoring after packaging

- **Full OCR Packaging**: `sage.spec` collects `rapidocr_onnxruntime`'s `config.yaml`, `.onnx` model files, and `onnxruntime` native libraries via `collect_data_files` + `collect_dynamic_libs`, ensuring OCR works after packaging

- **Process Management**: on app close, forcibly terminates the backend process via `taskkill /pid {pid} /f /t` to ensure port release

For the detailed packaging flow, see [`web/electron/`](web/electron/) and [`sage.spec`](sage.spec).

***

## Version Management

Sage uses a **single source of truth** for version numbers: the [`VERSION`](VERSION) file at the project root is the sole authority; all other locations automatically sync from it.

### Sync Topology

```
              ┌─────────────────────┐
              │  /VERSION  (1.2.5)  │   ← sole authority
              └──────────┬──────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Python       │  │ Frontend     │  │ Electron     │
│ backend      │  │ build        │  │ main process │
│ sage.__ver__ │  │ Vite         │  │ SAGE_VER     │
│ (read-fallback)│  │ __APP_VER__ │  │ (fallback)   │
└──────────────┘  └──────────────┘  └──────────────┘
        │                │                │
        ▼                ▼                ▼
    pyproject.toml   web/package.json  web/electron/main.cjs
   (dynamic version)  (prebuild sync)   (fallback)
```

### Bumping the Version

```bash
# View the current version
python scripts/bump_version.py show

# Patch / Minor / Major upgrades
python scripts/bump_version.py patch    # 1.2.5 -> 1.2.6
python scripts/bump_version.py minor    # 1.2.5 -> 1.3.0
python scripts/bump_version.py major    # 1.2.5 -> 2.0.0

# Pre-release version
python scripts/bump_version.py pre --tag rc

# Set directly
python scripts/bump_version.py set 1.2.3
```

`bump_version.py` auto-updates the `VERSION` file, appends a `CHANGELOG.md` entry, and calls `sync_version.py` to cascade-sync to `web/package.json`, `web/electron/main.cjs`, etc.

### Auto-Sync on Frontend Build

`web/package.json` configures prebuild/prepack/predev hooks; `npm run build` / `npm run dev` / `npm pack` auto-call the sync script with no manual intervention.

### CI Consistency Check

```yaml
- name: Check version consistency
  run: python scripts/sync_version.py --check
```

***

## CLI Commands

Invoked via `sage <command>` (built on Typer + Rich):

| Command | Description |
| --- | --- |
| `sage init` | First-time config wizard (choose Provider, fill in API Key, generate .env) |
| `sage chat [PROMPT]` | Interactive chat (no args enters REPL; with args runs once then exits) |
| `sage serve` | Start the HTTP API service (default `127.0.0.1:8000`) |
| `sage index` | Index papers in the current workspace |
| `sage stats` | Show system stats (memory / index / tool calls) |
| `sage collaborate` | Writing-mode demo (intelligent flow selection) |
| `sage version` | Show version info and compare with PyPI |
| `sage update` | Auto-upgrade to the latest version |

### Built-in Commands in chat Mode

Available in `sage chat` interactive mode:

- `/help` — view available commands

- `/clear` — clear the current context

- `/tokens` — view current token usage

- `/index` — trigger workspace indexing

- `/stats` — show system stats

- `/exit` or `/quit` — exit

***

## HTTP API

After starting `sage serve`, all APIs listen on `http://127.0.0.1:8000` by default. Full OpenAPI docs are at `/docs`.

### Sage Workspace API (12 endpoints)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/sage/workspaces` | Create a workspace |
| GET | `/api/sage/workspaces` | List all workspaces |
| GET | `/api/sage/workspaces/{ws_id}` | Get workspace details |
| DELETE | `/api/sage/workspaces/{ws_id}` | Delete a workspace |
| POST | `/api/sage/workspaces/{ws_id}/import-folder` | Batch-import papers from a folder |
| POST | `/api/sage/workspaces/{ws_id}/upload` | Upload a single paper file |
| POST | `/api/sage/workspaces/{ws_id}/index` | Trigger vectorized indexing |
| GET | `/api/sage/workspaces/{ws_id}/index-status` | Query index status |
| POST | `/api/sage/workspaces/{ws_id}/switch` | Switch to this workspace |
| GET | `/api/sage/workspaces/{ws_id}/papers` | List papers in the workspace |
| GET | `/api/sage/workspaces/{ws_id}/papers/download` | Download a specified paper |
| DELETE | `/api/sage/workspaces/{ws_id}/papers?path=xxx` | Delete a specified paper (with path-traversal protection) |

### Sage Paper Tool API (6 endpoints)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/sage/search` | Semantic search over the workspace literature library |
| POST | `/api/sage/extract-references` | Extract references from a paper |
| POST | `/api/sage/format-references` | Format references per the target journal |
| POST | `/api/sage/check-plagiarism` | Plagiarism check |
| POST | `/api/sage/search-external` | External academic data source search |
| GET | `/api/sage/citation-styles` | Get supported citation format list |

### General Agent API

| Method | Path | Description |
| --- | --- | --- |
| POST | `/chat/stream` | Streaming chat (SSE; unified pipeline with automatic intent-analysis routing: single Agent for simple tasks / multi-agent for complex tasks; `mode` deprecated. Event types: `tool_start` / `tool_result` / `text` / `reasoning` / `collaborate` / `retry` / `progress` / `context_usage` / `intent_confirm_required` / `delete_confirm_required` / `error` / `done`; request body supports `pool_mode` / `force_role` / `force_complexity` / `confirmed_intent` / `intent_correction`) |
| GET | `/conversations` | List history conversations |
| POST | `/conversations` | Create a new conversation |
| GET | `/conversations/{id}/messages` | Get conversation messages |
| DELETE | `/conversations/{id}` | Delete a conversation |
| POST | `/index` | Index the current workspace |
| GET | `/api/agent/info` | Agent info |
| GET | `/api/agents` | List all agent roles |
| GET | `/api/workspace` | Current workspace info |
| GET | `/api/workspace/tree` | Workspace file tree |
| POST | `/api/workspace` | Create/write workspace file |
| GET | `/api/tools` | List all tools |
| GET | `/api/skills` | List installed skills |
| GET | `/api/skills/remote-search` | Remote skill search |
| POST | `/api/skills/install` | Install a remote skill |
| GET | `/api/skills/manifest` | Skill manifest |
| GET | `/api/models` | Available model list |
| GET/POST | `/api/user-settings` | User settings (including optional `tavilyApiKey`) |
| POST | `/api/model/preload` | Preload a model |
| GET | `/api/model/download-progress` | Model download progress |
| GET | `/api/token-stats` | Token usage stats |
| GET | `/api/conversation/{id}/context-usage` | Query a specified conversation's context usage & compression stats (current occupied / max window / compression threshold / compressed rounds / cumulative saved) |
| POST | `/api/conversation/{id}/recheck-context` | Recheck whether context exceeds the limit after a model switch; compress immediately if over and return the latest usage |
| GET | `/api/version/check` | Check for version updates |
| POST | `/api/version/download` | Download a new version (SSE streaming progress) |
| POST | `/api/version/install` | Install a new version |
| GET | `/memory/stats` | Memory system stats |
| GET | `/memory/memories` | List long-term memories |
| POST | `/memory/search` | Semantic memory search |
| GET | `/memory/summaries` | List conversation summaries |
| GET | `/health` | Health check |

### Draft Review API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/review/drafts` | Draft list page: all conversations with drafts (title, topic, word-count progress, update time) |
| GET | `/api/review/draft?conversation_id=xxx` | Get a specified conversation's draft full text and chapter structure (reads workspace root for legacy drafts when no ID is passed) |
| POST | `/api/review/detect-ai` | AI pattern detection (requires `conversation_id`) |
| POST | `/api/review/rewrite` | Deep rewrite (requires `conversation_id`, carrying rewrite scope/requirements) |
| POST | `/api/review/data-fill` | `【数据】` placeholder backfill — directly replaces that placeholder by in-section sequence number, pure replacement without LLM (requires `conversation_id`) |
| POST | `/api/review/revise` | Targeted chapter revision (requires `conversation_id`, with retention-rate check to prevent shrinkage) |
| POST | `/api/review/lock` | Lock/unlock chapters (requires `conversation_id`) |
| GET | `/api/review/versions` | List all version snapshots (new→old, with reason/time/word count) |
| POST | `/api/review/versions/restore` | Overwrite the current draft with a specified snapshot and save (rollback) |
| POST | `/api/review/export-docx` | Export the current draft to `.docx` (in outline order, skipping meaningless chapters) |
| POST | `/api/review/verify-references` | Reference authenticity hard check (per-entry CrossRef/Weipu/Wanfang, deterministic, no LLM) |
| GET | `/api/review/citation-report` | Read the most recent reference verification report (no re-networking) |
| POST | `/api/review/fix-vertical-messages` | Fix vertical/broken-layout tables in history messages (`dry_run=true` previews without writing to DB) |

***

## Development & Testing

### Install Development Dependencies

```bash
pip install -e ".[dev,paper,embed]"
```

### Run Tests

Tests use the standard-library `unittest` style, located in `tests/`, run directly with Python (requires `PYTHONPATH` pointing to `src`):

```bash
# Windows (PowerShell)
$env:PYTHONPATH="src"
python tests/test_paper_project.py      # single test file
python tests/test_paper_quality.py      # quality gate boundary tests
python tests/test_paper_export.py       # LaTeX/Word export tests
python tests/test_paper_data.py         # data placeholder scan tests
python tests/test_orchestrator_context.py  # orchestrator end-to-end tests
python tests/test_intent.py             # intent analysis tests

# macOS / Linux
PYTHONPATH=src python tests/test_paper_project.py
```

The current test suite covers the following modules:

| Test file | Coverage |
| --- | --- |
| `test_paper_project.py` | PaperProject shared draft (outline/material/per-section storage, save/load roundtrip, finalize, read_draft consistency) |
| `test_paper_quality.py` | Deterministic quality gate (chapter completeness, `[CITE:]` residue, reference existence, word budget, `### References` boundary) |
| `test_paper_export.py` | LaTeX/Word export (title/bold/list/citation escaping, empty draft, special chars, real `.docx` generation) |
| `test_paper_data.py` | `【数据】` placeholder scan (positioning, context truncation, chapter inference) |
| `test_orchestrator_context.py` | Orchestrator end-to-end (intent→plan→outline→cost→batches→quality gate→revision→soft review→data suggestions→export, with LLM mock) |
| `test_intent.py` | Intent analysis (rule rapid judgment + verb-object pattern matching routing) |

### Test Isolation Design

- Temp data is fixed in `_test_data/tmp_testdata/` (created with `os.makedirs`, cleaned with `shutil.rmtree`), avoiding sandbox interception of `tempfile.mkdtemp`

- Test cases point `os.environ["SAGE_DATA_DIR"]` to an independent working directory, avoiding global-config pollution

- The orchestrator end-to-end test uses `_FakeWorker` and mock functions to replace `_analyze_intent` / `_generate_execution_plan` / `_generate_outline` / `_get_worker_by_role_name`, avoiding real LLM calls

- Export tests generate a real `.docx` in environments with `python-docx` installed and reopen to validate content; when not installed, verify a `RuntimeError` with installation guidance is raised

### Code Standards

- Python ≥ 3.11, using `from __future__ import annotations` for deferred annotations

- Complete type annotations (Pydantic + dataclass)

- Chinese docstrings and comments

- All public interfaces maintain backward compatibility

***

## License

[MIT License](LICENSE)

This project is licensed under the accompanying MIT license. You are free to use, copy, modify, distribute, and commercially use this project, provided you retain the original copyright and license notice. Third-party dependencies (models, icons, fonts, etc.) remain the property of their respective owners and are governed by their own license terms.

***

## Acknowledgments

The Sage system is built on the following open-source projects:

- [FastAPI](https://fastapi.tiangolo.com/) — Web framework

- [Pydantic](https://pydantic.dev/) — Data validation

- [Typer](https://typer.tiangolo.com/) — CLI framework

- [Rich](https://rich.readthedocs.io/) — Terminal styling

- [sentence-transformers](https://www.sbert.net/) — Local Embedding

- [OpenAI Python SDK](https://github.com/openai/openai-python) — LLM client

- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF parsing

- [python-docx](https://python-docx.readthedocs.io/) — Word parsing

- [Electron](https://www.electronjs.org/) — Desktop framework

- [Vue 3](https://vuejs.org/) — Frontend framework