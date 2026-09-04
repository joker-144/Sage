"""
多 Agent 协同编排器 — Sage 论文写作系统（主控+平等协作+整理汇报+多重验证）

核心设计:
- 主编 Agent (Supervisor/Orchestrator): 分析写作需求 → 拆解任务 → 调度子智能体 → 质量把关
- 平等协作子智能体:
  - 文献调研员 (Literature): 文献检索、综述、研究现状分析
  - 方法论专家 (Methodology/Planner): 研究方法设计、实验方案、论证框架
  - 撰写员 (Writer/Coder): 论文各章节具体撰写
- 整理汇报员 (Consolidator): 整合讨论产出，消除重复矛盾
- 引用管理员 (Citation): 引用插入、参考文献格式化、查重
- 审校核查员 (Verifier/Reviewer): 多重验证（文献库+逻辑+外部检索+学术规范）
- 修订员 (Reviser/Debugger): 根据审校报告修复问题

工作流程:
  1. 主编接收用户写作需求，拆解为子任务
  2. 文献调研员检索文献，方法论专家设计方法，撰写员撰写内容（平等协作讨论）
  3. 整理汇报员整合各子智能体产出，形成连贯论文
  4. 引用管理员处理引用与格式化
  5. 审校核查员执行多重验证，生成审校报告
  6. 如有问题，修订员修复，再交审校核查员重检
  7. 最终结果输出给用户

与单 Agent Loop 的关系:
  Orchestrator 内部使用 AgentLoop 作为 Worker 的执行引擎，
  每个 Worker 是独立的 AgentLoop 实例，拥有自己的上下文和工具访问权限。

智能体定义从 agents/ 文件夹的 agent.json 加载（见 loader.py），
专属技能自动注入到 Worker 的 system prompt。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Optional

from sage.agent.loop import AgentLoop, LoopEvent
from sage.citation_verify import verify_references
from sage.config import get_config
from sage.agents.loader import get_agent_loader
from sage.llm.client import LLMClient
from sage.paper_project import PaperProject, DEFAULT_OUTLINE, estimate_paper_cost, get_outline_for_type
from sage.paper_quality import run_quality_checks

logger = logging.getLogger(__name__)


class AgentRole(Enum):
    """Agent 角色（Sage 论文写作系统）

    保留原有角色值用于向后兼容，同时新增 Sage 专用角色。
    原有角色在 Sage 中映射为：
      SUPERVISOR → 主编/Orchestrator
      PLANNER → 方法论专家/Methodology
      CODER → 撰写员/Writer
      REVIEWER → 审校核查员/Verifier
      DEBUGGER → 修订员/Reviser
    """
    # 原有角色（向后兼容）
    SUPERVISOR = "supervisor"
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    DEBUGGER = "debugger"
    # Sage 新增角色
    LITERATURE = "literature"      # 文献调研员
    CITATION = "citation"          # 引用管理员
    CONSOLIDATOR = "consolidator"  # 整理汇报员


@dataclass
class SubTask:
    """子任务定义"""
    id: str
    role: AgentRole
    description: str
    context: str = ""
    dependencies: list[str] = field(default_factory=list)
    result: str = ""
    status: str = "pending"


@dataclass
class CollaborationEvent:
    """协同事件（供 CLI/Web 展示）"""
    type: str  # "task_created" | "worker_start" | "worker_done" | "reflection" | "text" | "reasoning" | "retry" | "progress" | "tool_start" | "tool_result" | "context_usage" | "done"
    role: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    tokens: dict = None  # 该轮 LLM 调用的 token 用量（用于工具卡片显示）

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = {}


@dataclass
class IntentResult:
    """意图分析结果"""
    complexity: str  # "simple" | "complex"
    role: str        # AgentRole.value 或 "general"（通用Agent）
    reason: str = ""


# 角色中文名映射（供事件展示）
_ROLE_LABELS = {
    "supervisor": "主编", "planner": "方法论专家", "coder": "撰写员",
    "reviewer": "审校核查员", "debugger": "修订员",
    "literature": "文献调研员", "citation": "引用管理员", "consolidator": "整理汇报员",
    "general": "通用助手",
}


class AgentOrchestrator:
    """多 Agent 协同编排器

    每个 Worker Agent 是独立的 AgentLoop 实例，
    拥有独立的上下文和角色化的 System Prompt（从 agents/{role}/agent.json 加载）。
    """

    @classmethod
    def get_all_role_info(cls) -> list[dict]:
        """获取所有 Agent 角色的信息（用于 API 动态展示）"""
        return get_agent_loader().get_all_role_info()

    def __init__(self, workspace: Optional[Path] = None, conversation_id: Optional[str] = None, pool_mode: bool = False):
        config = get_config()
        self.workspace = workspace or config.workspace
        self.conversation_id = conversation_id
        self.pool_mode = pool_mode
        self._workers: dict[AgentRole, AgentLoop] = {}
        self._general_worker: Optional[AgentLoop] = None  # 通用 Agent（不绑定角色）
        self._history: list[SubTask] = []
        self._loader = get_agent_loader()
        self._llm = LLMClient()  # 用于意图分析（不带工具的快速调用）
        # 共享草稿文档：多智能体协作的全文事实来源（不再用 2000 字符残片传递）
        # 按对话隔离草稿：不同对话的论文写到 .sage/papers/{conversation_id}/ 下，
        # 互不覆盖；无 conversation_id 时回退工作区根目录旧稿（兼容旧版本单草稿）。
        self.project = self._create_project()
        # 跨会话持久草稿：加载上次的 paper_project.json / paper.md
        self.project.load()

    def _configure_worker(self, worker: AgentLoop) -> AgentLoop:
        """统一配置 worker：恢复对话历史 + 应用池模式检索路由"""
        # 恢复该对话的历史消息，保持多轮上下文（写作/单Agent 统一链路后所有对话可续聊）
        if self.conversation_id:
            try:
                worker.restore_history_from_db()
            except Exception:
                pass
        # 池模式：将 search_literature 替换为跨工作空间检索
        if self.pool_mode:
            from sage.tools.engine import SEARCH_LITERATURE_SCHEMA
            from sage.tools.paper_ops import pool_search_literature
            worker.tools.register("search_literature", pool_search_literature, SEARCH_LITERATURE_SCHEMA)
        return worker

    def _create_project(self) -> PaperProject:
        """按对话创建隔离的 PaperProject

        - 传了 conversation_id：草稿落在 <workspace>/.sage/papers/{conversation_id}/，
          不同对话各自一篇论文，互不干扰。
        - 未传 conversation_id：沿用工作区根目录的 paper.md（旧版单草稿兼容）。
        """
        if self.conversation_id:
            papers_dir = self.workspace / ".sage" / "papers" / str(self.conversation_id)
            return PaperProject(
                workspace=self.workspace,
                meta_path=papers_dir / "paper_project.json",
                draft_path=papers_dir / "paper.md",
            )
        return PaperProject(workspace=self.workspace)

    def _get_worker(self, role: AgentRole) -> AgentLoop:
        """获取或创建 Worker Agent

        system prompt 从 agents/{role}/agent.json 加载，并自动注入专属技能。
        worker 是编排器内部角色，persist=False：不写入历史对话列表，
        避免多个 worker 产生大量无意义对话记录污染对话历史。
        """
        if role not in self._workers:
            prompt = self._loader.get_system_prompt(role.value)
            if not prompt:
                # 回退：loader 中没有定义时使用空字符串
                prompt = ""
            self._workers[role] = AgentLoop(
                workspace=self.workspace,
                system_prompt=prompt,
                writing_mode=True,
                persist=False,
            )
            self._configure_worker(self._workers[role])
        return self._workers[role]

    def _get_general_worker(self) -> AgentLoop:
        """获取通用 Agent（不绑定特定角色 prompt）

        用于意图分析判定为简单任务且无匹配角色时的兜底处理。
        """
        if self._general_worker is None:
            # 不传 system_prompt，AgentLoop 会使用默认的通用 system prompt
            self._general_worker = AgentLoop(workspace=self.workspace, writing_mode=True, persist=False)
            self._configure_worker(self._general_worker)
        return self._general_worker

    def _get_worker_by_role_name(self, role_name: str) -> Optional[AgentLoop]:
        """根据角色名获取 Worker，'general' 返回通用 Agent，未知角色返回 None"""
        if role_name == "general":
            return self._get_general_worker()
        try:
            role = AgentRole(role_name)
            return self._get_worker(role)
        except ValueError:
            return None

    async def collaborate(self, user_input: str, force_role: Optional[str] = None) -> AsyncIterator[CollaborationEvent]:
        """写作模式入口 — 智能选择流程

        流程:
          1. 意图分析（规则快速判断 + 不确定时 LLM 精细分析）
          2. 简单任务 → 根据意图选择匹配角色 Agent 或通用 Agent
          3. 复杂任务 → 完整多智能体协作流程（主编→文献→方法→撰写→整理→引用→审校→修订）

        Args:
            user_input: 用户原始需求
            force_role: 用户显式指定处理角色（如"literature"/"coder"），
                        提供时跳过意图分析、按简单任务路由到该角色
        """
        # Step 1: 意图分析（force_role 时跳过，用户已明确指定角色）
        if force_role:
            intent = IntentResult(
                complexity="simple",
                role=force_role,
                reason="用户指定角色",
            )
            yield CollaborationEvent(
                type="task_created",
                role="supervisor",
                content=f"用户指定角色: {_ROLE_LABELS.get(force_role, force_role)}",
            )
        else:
            # 澄清回路：信息严重不足时反问，而非盲目开工
            if _needs_clarification(user_input, has_draft=bool(self.project.read_draft().strip())):
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=(
                        "请求信息不足，请补充后再试。例如：论文的主题/方向是什么？"
                        "需要完整论文，还是某个部分（摘要/目录/引言/结论/某一章节）？"
                    ),
                )
                yield CollaborationEvent(type="done", role="supervisor")
                return
            yield CollaborationEvent(
                type="task_created",
                role="supervisor",
                content=f"正在分析用户意图: {user_input[:100]}",
            )
            intent = await self._analyze_intent(user_input)

        role_label = _ROLE_LABELS.get(intent.role, intent.role)
        yield CollaborationEvent(
            type="reflection",
            role="supervisor",
            content=f"意图分析结果: {'复杂任务(多智能体协作)' if intent.complexity == 'complex' else '简单任务'} → {role_label}。理由: {intent.reason}",
        )

        # 断点续写：已有草稿 + 续写意图 → 续写未完成章节（优先于常规路由）
        if _is_continuation(user_input) and self.project.read_draft().strip():
            missing = self.project.missing_sections()
            if not missing:
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content="所有章节均已完成，如需修改请使用修订指令（如“把结论改保守”）。",
                )
                yield CollaborationEvent(type="done", role="supervisor")
                return
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content=f"断点续写：基于已有草稿继续撰写 {len(missing)} 个未完成章节...",
            )
            worker = self._get_worker_by_role_name("coder") or self._get_general_worker()
            yield CollaborationEvent(
                type="worker_start",
                role="coder",
                content="撰写员继续撰写未完成章节...",
            )
            prompt = (
                f"## 当前论文草稿（已完成部分）\n{self.project.read_draft()}\n\n"
                f"## 尚未完成的章节\n{self.project.missing_sections_text()}\n\n"
                "请续写上述未完成的章节，输出续写内容"
                "（markdown，每个章节以 '## 章节标题' 开头，遵守字数预算）。"
            )
            continued_parts: list[str] = []
            async for event in worker.run(prompt):
                mapped = self._map_event(event, "coder")
                if mapped:
                    yield mapped
                if event.type == "text":
                    continued_parts.append(event.content)
            continued = _join_stream_text(continued_parts)
            if continued.strip():
                self.project.parse_draft_to_sections(continued)
                self.project.store_material("coder", continued)
                path = self.project.finalize()
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=f"续写完成，草稿已更新: {path}（{self.project.outline_progress()}）",
                )
            yield CollaborationEvent(type="done", role="supervisor")
            return

        # Step 2: 简单任务 — 路由到匹配角色 Agent 或通用 Agent
        if intent.complexity == "simple":
            worker = self._get_worker_by_role_name(intent.role)
            if worker is None:
                # 未知角色兜底为通用 Agent
                worker = self._get_general_worker()
                intent.role = "general"
            yield CollaborationEvent(
                type="worker_start",
                role=intent.role,
                content=f"由 {role_label} 处理简单任务",
            )

            # 简单任务统一注入当前草稿（撰写/修订类角色；P1-⑤ 泛化，替代原 debugger 特判）
            uses_draft = intent.role in ("coder", "debugger") and bool(self.project.read_draft().strip())
            prompt = _build_simple_prompt(intent.role, user_input, self.project)
            if uses_draft:
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=f"{role_label}基于已有草稿处理请求...",
                )

            out_parts: list[str] = []
            async for event in worker.run(prompt):
                mapped = self._map_event(event, intent.role)
                if mapped:
                    yield mapped
                if event.type == "text":
                    out_parts.append(event.content)

            # 撰写/修订结果写回共享草稿并落盘
            if uses_draft and out_parts:
                output = _join_stream_text(out_parts)
                self.project.parse_draft_to_sections(output)
                self.project.store_material(intent.role, output)
                path = self.project.finalize()
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=f"草稿已更新: {path}",
                )

            yield CollaborationEvent(type="done", role="supervisor")
            return

        # Step 3: 复杂任务 — 主编动态调度子智能体（按批次并行）
        try:
            # 全新完整论文：清空上次草稿，避免旧内容串味
            # （多轮修订走上面的简单任务 debugger 分支，不清空）
            self.project.clear()

            # 主编生成执行计划
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="主编正在分析需求，生成执行计划...",
            )
            plan = await self._generate_execution_plan(user_input)

            # 展示执行计划
            batches = plan.get("batches", [])
            plan_desc_parts = []
            for batch in batches:
                roles = batch.get("roles", [])
                role_labels = [_ROLE_LABELS.get(r, r) for r in roles]
                plan_desc_parts.append(f"批次{batch.get('id', '?')}: {' + '.join(role_labels)}（并行）")
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content=f"执行计划:\n" + "\n".join(plan_desc_parts),
            )

            # 大纲先行：主编生成结构化大纲（按论文类型差异化 + 字数预算），
            # 后续撰写/整理/审校据此逐节进行，保证结构与篇幅可控
            paper_type = _detect_paper_type(user_input)
            outline_sections = await self._generate_outline(user_input, paper_type=paper_type)
            self.project.set_outline(outline_sections)
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="论文大纲:\n" + self.project.outline_text(),
            )

            # 生成前成本/长度预估：反馈全文规模与预估调用量
            cost = estimate_paper_cost(self.project.get_outline())
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content=(
                    f"预估规模：全文约 {cost['total_target_words']} 字"
                    f" / {cost['section_count']} 个章节，"
                    f"预计约 {cost['est_llm_calls']} 次模型调用。"
                ),
            )

            # 按批次执行（共享草稿：worker 产出写入 project，下游读全文）
            batch_results: dict[str, str] = {}  # 累积各角色产出，供后续批次依赖
            batch_id_to_roles = {b.get("id"): b.get("roles", []) for b in batches}
            for batch in batches:
                roles = batch.get("roles", [])
                if not roles:
                    continue
                # 方案A：依赖角色由 depends_on 批次推导，真正驱动下游上下文
                dep_roles: list[str] = []
                for dep_id in batch.get("depends_on", []):
                    dep_roles.extend(batch_id_to_roles.get(dep_id, []))
                dep_roles = list(dict.fromkeys(dep_roles))  # 去重保序
                # 同一批次并行执行
                async for event in self._run_parallel_workers(
                    roles, user_input, batch_results,
                    project=self.project, dep_roles=dep_roles,
                ):
                    yield event

            # 最终质量检查
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="主编执行最终质量检查...",
            )

            # 成稿落盘到工作空间 paper.md
            draft_path = self.project.finalize()
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content=(
                    f"完整论文已保存到 {draft_path}（约 {self.project.draft_word_count()} 字）。"
                    f"{self.project.outline_progress()}"
                ),
            )
            # 导出 LaTeX 版（可选，失败不影响流程）
            try:
                tex_path = self.project.export_latex()
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=f"LaTeX 版已导出到 {tex_path}",
                )
            except Exception as e:
                logger.warning("LaTeX 导出失败: %s", e)

            # 确定性质量门：不依赖 LLM 的硬校验，成稿后反馈待改进项
            plan_roles = {r for b in batches for r in b.get("roles", [])}
            references_expected = ("citation" in plan_roles)
            report = run_quality_checks(
                self.project,
                references_expected=references_expected,
            )
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content=report.to_text(),
            )

            # 二次复核闭环：质量门发现"可修复"问题 → 修订员修订 → 再查（上限 2 轮）
            for round_no in range(1, _REVISION_MAX_ROUNDS + 1):
                actionable = [
                    i for i in report.issues if i.code in _REVISION_ACTIONABLE_CODES
                ]
                if not actionable:
                    break
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=f"第 {round_no} 轮修订：修订员修复 {len(actionable)} 项问题...",
                )
                revised = await self._revise_round(report)
                if not revised.strip():
                    break
                # 定点修订有效性校验：自本轮起修订员只输出【被修改的章节】，
                # 因此不再用"全文长度"判断是否缩水，改为校验输出能被解析出章节标题，
                # 防止 LLM 输出被截断/遗漏成纯文本时静默丢内容。
                if not re.search(r"(?m)^#{2,4}\s+", revised.strip()):
                    logger.warning("修订产出未包含任何章节标题，判定为不完整，拒绝覆盖本轮草稿")
                    yield CollaborationEvent(
                        type="reflection",
                        role="supervisor",
                        content=f"第 {round_no} 轮修订产出未包含任何章节标题，判定为不完整，已保留原草稿",
                    )
                    break
                # 定点覆盖：仅更新修订输出中出现的章节，其余章节保留原样
                self.project.parse_draft_to_sections(revised)
                self.project.store_material("debugger", revised)
                self.project.finalize()
                report = run_quality_checks(
                    self.project,
                    references_expected=references_expected,
                )
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content=report.to_text(),
                )

            # 引用真实性硬校验：逐条 CrossRef/维普/万方验证参考文献（确定性步骤，不依赖 LLM）。
            # 网络失败不阻塞成稿，仅标记 network_error；报告持久化到 paper_project.json
            if references_expected:
                yield CollaborationEvent(
                    type="reflection",
                    role="supervisor",
                    content="引用管理员逐条验证参考文献真实性...",
                )
                try:
                    cit_report = await asyncio.wait_for(
                        verify_references(
                            self.project.read_draft(),
                            workspace=self.project.workspace,
                        ),
                        timeout=240.0,
                    )
                    self.project.citation_report = cit_report.to_dict()
                    self.project.save()
                    yield CollaborationEvent(
                        type="reflection",
                        role="citation",
                        content=cit_report.to_text(),
                        metadata={"citation_report": cit_report.to_dict()},
                    )
                except Exception as e:
                    logger.warning("引用真实性验证失败（不影响成稿）: %s", e)
                    yield CollaborationEvent(
                        type="reflection",
                        role="supervisor",
                        content="引用真实性验证未完成（网络原因，不影响成稿，可稍后在审阅工作台重试）",
                    )

            # 软复核二次跑：修订后再让审校核查员（LLM）验证一遍
            yield CollaborationEvent(
                type="reflection",
                role="supervisor",
                content="审校核查员对修订稿执行二次复核...",
            )
            review_report = await self._review_round()
            if review_report.strip():
                self.project.store_material("reviewer_final", review_report)
                yield CollaborationEvent(
                    type="text",
                    role="reviewer",
                    content=review_report,
                )

            # 数据占位建议：扫描【数据】占位，生成数据处理与来源建议
            data_report = await self._generate_data_suggestions()
            if data_report.strip():
                yield CollaborationEvent(
                    type="text",
                    role="supervisor",
                    content="## 数据需求建议\n" + data_report,
                )

        except Exception as e:
            logger.warning("多智能体协作流程出错: %s", e)
            yield CollaborationEvent(
                type="worker_done",
                role="supervisor",
                content=f"协同过程出错: {e}",
                metadata={"error": str(e)},
            )

        yield CollaborationEvent(type="done", role="supervisor")

    async def _run_worker(self, role: AgentRole, prompt: str) -> str:
        """运行一个 Worker 并收集文本输出

        若 Worker 仅调工具未输出文本，生成有意义的工具调用摘要。
        """
        worker = self._get_worker(role)
        results = []
        tool_names = []
        async for event in worker.run(prompt):
            if event.type == "text":
                results.append(event.content)
            elif event.type == "tool_start" and event.tool_name:
                tool_names.append(event.tool_name)

        if results:
            return "\n".join(results)
        if tool_names:
            return f"[工具调用摘要] Worker 完成 {len(tool_names)} 次工具调用: {', '.join(tool_names[:10])}"
        return ""

    async def _analyze_intent(self, user_input: str) -> IntentResult:
        """意图分析 — 长输入 LLM 主判 + 短输入规则快判（P2-⑨）

        - 长输入（>=30 字）更易被规则误命中 → 交给 LLM 主判，规则/复杂任务兜底
        - 短输入：规则快速判断优先，避免 LLM 调用延迟

        Returns:
            IntentResult: 含 complexity(simple/complex)、role(角色名)、reason(判断理由)
        """
        # ── 长输入：LLM 主判，规则兜底 ──
        if len(user_input) >= 30:
            try:
                return await self._analyze_intent_with_llm(user_input)
            except Exception as e:
                logger.warning("长输入 LLM 意图分析失败，回退规则: %s", e)
                quick = self._quick_classify(user_input)
                if quick is not None:
                    return quick
                return IntentResult(
                    complexity="complex", role="supervisor",
                    reason="LLM 分析失败，降级为复杂任务",
                )

        # ── 短输入：规则快判优先，未命中再 LLM ──
        quick = self._quick_classify(user_input)
        if quick is not None:
            return quick
        try:
            return await self._analyze_intent_with_llm(user_input)
        except Exception as e:
            # LLM 分析失败时降级为复杂任务（多智能体兜底，确保不漏）
            logger.warning("意图分析 LLM 调用失败，降级为复杂任务: %s", e)
            return IntentResult(
                complexity="complex",
                role="supervisor",
                reason=f"意图分析 LLM 调用失败，降级为复杂任务: {e}",
            )

    def _quick_classify(self, user_input: str) -> Optional[IntentResult]:
        """快速规则判断 — 基于动词+宾语模式直接匹配智能体，不确定返回 None 触发 LLM 分析

        优先级:
          1. 论文局部写作（"论文的摘要/目录/某章"）→ simple + coder
          2. 明确的多智能体协作任务（写一篇完整论文/多章节/开题报告等）→ complex
          3. 精细的动词+宾语模式匹配 → simple + 具体角色（如"生成目录"→coder）
          4. 问候/简单对话 → simple + general
          5. 短问题兜底匹配 → simple + 匹配角色
          6. 不确定 → None，交给 LLM 精细分析
        """
        lower = user_input.lower().strip()

        # ── 第一优先级：修订意图（修改/润色 + 论文/段落/内容等 → 修订员）──
        # 先于"论文局部写作"判断，避免"修改论文结论"被误判为撰写
        revise_actions = ("修改", "修订", "润色", "调整", "修复")
        revise_objects = ("论文", "内容", "段落", "章节", "语句", "结论", "引言", "摘要", "前言", "目录", "正文")
        if any(a in lower for a in revise_actions) and any(o in lower for o in revise_objects):
            return IntentResult(
                complexity="simple",
                role="debugger",
                reason="修订意图，由修订员处理",
            )

        # ── 第二优先级：论文局部写作 ──
        # "论文的摘要/目录/引言/第N章" 是局部撰写，先于"完整论文"判断，避免误路由到多智能体
        if _PARTIAL_OF_PAPER_RE.search(lower):
            return IntentResult(
                complexity="simple",
                role="coder",
                reason="论文局部写作（摘要/目录/某章节），由撰写员处理",
            )

        # ── 第三优先级：明确的多智能体协作任务 ──
        complex_keywords = [
            "完整论文", "多章节", "写一篇论文", "撰写一篇论文", "帮我写论文",
            "ssci", "cssci", "期刊投稿", "开题报告",
            "毕业论文", "学位论文", "综述论文", "写一篇 paper", "survey paper",
        ]
        # sci/ei 需边界匹配（避免 "science"/"their" 等误命中）
        is_complex = any(kw in lower for kw in complex_keywords) or bool(
            re.search(r"\b(sci|ei)\b", lower) or re.search(r"(sci|ei)[\u4e00-\u9fff]", lower)
        )
        if is_complex:
            return IntentResult(
                complexity="complex",
                role="supervisor",
                reason="包含完整论文写作关键词，需要多智能体协作",
            )

        # ── 第四优先级：精细的动词+宾语模式匹配 → 直接分配智能体 ──
        role = self._match_role_by_patterns(user_input)
        if role is not None:
            return IntentResult(
                complexity="simple",
                role=role,
                reason=f"规则匹配到 {_ROLE_LABELS.get(role, role)} 处理",
            )

        # ── 第五优先级：问候/简单对话 ──
        simple_greetings = ["你好", "hello", "hi ", "hey", "在吗", "谢谢", "thanks"]
        if any(lower.startswith(g) for g in simple_greetings) or lower in simple_greetings:
            return IntentResult(
                complexity="simple",
                role="general",
                reason="问候或简单对话，由通用助手处理",
            )

        # ── 第六优先级：短问题兜底匹配 ──
        if len(user_input) < 30:
            fallback_role = self._match_role_by_keywords(user_input)
            return IntentResult(
                complexity="simple",
                role=fallback_role,
                reason="短问题，由匹配角色处理",
            )

        # ── 不确定 — 交给 LLM 分析 ──
        return None

    def _match_role_by_patterns(self, text: str) -> Optional[str]:
        """基于动词+宾语模式精细匹配智能体角色，无匹配返回 None

        优先级：撰写指令 > 文献检索 > 引用处理 > 审校 > 修订
        （撰写指令优先，避免用户问题中残留的"文献调研"前缀干扰真实写作意图）
        """
        lower = text.lower()

        # 撰写员(coder)：生成/写/撰写 + 目录/摘要/章节/前言/结论等
        writing_actions = ["生成", "写", "撰写", "起草", "输出", "给", "提供", "列"]
        writing_objects = [
            "目录", "摘要", "前言", "引言", "结论", "正文", "章节", "大纲",
            "框架", "abstract", "outline", "内容", "标题",
        ]
        if any(a in lower for a in writing_actions) and any(o in lower for o in writing_objects):
            return "coder"

        # 文献调研员(literature)：检索/查找/调研 + 文献/资料/相关研究
        lit_actions = ["检索", "查找", "调研", "搜索", "搜集", "收集"]
        lit_objects = ["文献", "资料", "相关研究", "相关论文", "literature"]
        if any(a in lower for a in lit_actions) and any(o in lower for o in lit_objects):
            return "literature"

        # 引用管理员(citation)：格式化/检查/核查 + 引用/参考文献
        cite_actions = ["格式化", "规范化", "整理", "检查", "核查", "验证"]
        cite_objects = ["引用格式", "参考文献格式", "citation", "引用规范", "参考文献", "引用"]
        if any(a in lower for a in cite_actions) and any(o in lower for o in cite_objects):
            return "citation"

        # 审校核查员(reviewer)：审校/审查/核查/查重
        review_actions = ["审校", "审查", "核查", "查重", "检查"]
        review_objects = ["逻辑", "规范", "重复", "学术不端", "质量"]
        if any(a in lower for a in review_actions) and any(o in lower for o in review_objects):
            return "reviewer"

        # 修订员(debugger)：修改/修订/修复 + 论文/内容/段落
        revise_actions = ["修改", "修订", "修复", "调整", "润色"]
        revise_objects = ["论文", "内容", "段落", "章节", "语句"]
        if any(a in lower for a in revise_actions) and any(o in lower for o in revise_objects):
            return "debugger"

        # 无匹配
        return None

    def _match_role_by_keywords(self, text: str) -> str:
        """根据问题关键词匹配最合适的角色，无匹配返回 'general'"""
        lower = text.lower()
        # 文献调研相关
        if any(w in lower for w in ["文献", "检索", "综述", "查找资料", "相关研究", "literature", "search"]):
            return "literature"
        # 方法论相关
        if any(w in lower for w in ["方法", "实验设计", "研究方法", "方案", "methodology", "design"]):
            return "planner"
        # 撰写相关
        if any(w in lower for w in ["写", "撰写", "润色", "改写", "段落", "write", "draft"]):
            return "coder"
        # 审校相关
        if any(w in lower for w in ["审校", "检查", "审查", "验证", "逻辑", "review", "check"]):
            return "reviewer"
        # 修订相关
        if any(w in lower for w in ["修改", "修订", "修复", "改", "fix", "revise"]):
            return "debugger"
        # 引用相关
        if any(w in lower for w in ["引用", "参考文献", "格式化", "citation", "reference"]):
            return "citation"
        # 整理相关
        if any(w in lower for w in ["整合", "整理", "合并", "统一", "consolidate"]):
            return "consolidator"
        # 无匹配 — 通用 Agent
        return "general"

    # ── 统一 LLM 调用（带轻量重试）──

    async def _llm_call(self, messages: list, max_tokens: int = 500):
        """统一的 LLM 调用入口（无工具），带轻量指数退避重试

        编排器的意图分析 / 计划生成 / 大纲生成等环节此前直连 self._llm 无重试，
        网络抖动等瞬时错误会导致整个流程直接降级。这里统一做 3 次尝试，
        重试耗尽后抛出原异常，由各调用方既有的降级逻辑接管（行为不变）。
        """
        last_error = None
        for attempt in range(1, 4):
            try:
                return await self._llm.achat_with_tools(
                    messages=messages,
                    tools=[],
                    max_tokens=max_tokens,
                )
            except Exception as e:
                last_error = e
                logger.warning("编排 LLM 调用失败（第 %d 次尝试）: %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
        raise last_error

    async def _analyze_intent_with_llm(self, user_input: str) -> IntentResult:
        """使用 LLM 进行精细意图分析

        通过一次不带工具的 LLM 调用，输出结构化 JSON 结果：
        {complexity, role, reason}

        - complexity: "simple"（单Agent可处理） | "complex"（需多智能体协作）
        - role: simple 时选择最匹配的角色；complex 时为 "supervisor"
        - reason: 判断理由（展示给用户）
        """
        system_prompt = """你是 Sage 论文写作系统的意图分析器。分析用户输入，判断任务复杂度并选择最合适的处理方式。

可用角色:
- literature: 文献调研员（文献检索、综述、研究现状分析）
- planner: 方法论专家（研究方法设计、实验方案、论证框架）
- coder: 撰写员（论文各章节具体撰写）
- reviewer: 审校核查员（多重验证、逻辑核查、学术规范检查）
- debugger: 修订员（根据审校报告修复问题）
- citation: 引用管理员（引用插入、参考文献格式化、查重）
- consolidator: 整理汇报员（整合产出、消除重复矛盾）
- general: 通用助手（不属于上述角色的简单问答/解释/通用任务）
- supervisor: 主编（复杂任务需多智能体协作时使用）

判断规则:
1. 复杂任务(complex): 需要完整论文写作、多章节撰写、系统性研究、多步骤协作的任务 → role="supervisor"
2. 简单任务(simple): 单一角色可完成的任务（如查询文献、润色段落、检查引用格式、解释概念等） → role=最匹配的角色

必须输出 JSON 格式（不要任何其他内容）:
{"complexity": "simple" 或 "complex", "role": "角色名", "reason": "简短理由(不超过30字)"}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户输入: {user_input}\n\n请分析意图并输出 JSON。"},
        ]

        # 限制 max_tokens 避免浪费（意图分析结果很短）
        response = await self._llm_call(messages, max_tokens=200)

        content = _extract_json(response.content or "") or ""
        if not content:
            # JSON 提取失败 — 降级为简单任务通用 Agent
            return IntentResult(
                complexity="simple",
                role="general",
                reason="意图分析结果解析失败，由通用助手处理",
            )

        try:
            data = json.loads(content)
            complexity = data.get("complexity", "simple")
            role = data.get("role", "general")
            reason = data.get("reason", "")

            # 校验 complexity 值
            if complexity not in ("simple", "complex"):
                complexity = "simple"
            # 校验 role 值
            valid_roles = {"literature", "planner", "coder", "reviewer", "debugger",
                           "citation", "consolidator", "general", "supervisor"}
            if role not in valid_roles:
                role = "general"
            # complex 任务强制 role=supervisor
            if complexity == "complex":
                role = "supervisor"

            return IntentResult(complexity=complexity, role=role, reason=reason)
        except (json.JSONDecodeError, KeyError):
            # JSON 解析失败 — 降级为简单任务通用 Agent
            return IntentResult(
                complexity="simple",
                role="general",
                reason="意图分析结果解析失败，由通用助手处理",
            )

    # ── 动态执行计划生成（主编根据用户需求按需分配子智能体） ──

    async def _generate_execution_plan(self, user_input: str) -> dict:
        """主编用 LLM 分析用户需求，生成动态执行计划

        计划格式:
        {
            "batches": [
                {"id": 1, "roles": ["literature", "planner"], "depends_on": []},
                {"id": 2, "roles": ["coder"], "depends_on": [1]},
                {"id": 3, "roles": ["consolidator"], "depends_on": [2]},
                {"id": 4, "roles": ["citation"], "depends_on": [3]},
                {"id": 5, "roles": ["reviewer"], "depends_on": [4]}
            ]
        }

        同一 batch 内的 roles 并行执行；batch 间按 depends_on 串行。
        每个子智能体只分配需要的，不强制全流程。
        """
        system_prompt = """你是 Sage 论文写作系统的主编调度器。根据用户需求，生成动态执行计划。

可用子智能体:
- literature: 文献调研员（文献检索、综述、研究现状分析）
- planner: 方法论专家（研究方法设计、实验方案、论证框架）
- coder: 撰写员（论文各章节具体撰写）
- consolidator: 整理汇报员（整合多份产出、消除重复矛盾、统一风格）
- citation: 引用管理员（引用插入、参考文献格式化、查重）
- reviewer: 审校核查员（多重验证、逻辑核查、学术规范检查）
- debugger: 修订员（根据审校报告修复问题）

调度原则:
1. 按需分配：只分配用户需求真正需要的子智能体，不强制全流程
2. 依赖关系：
   - coder 依赖 literature 和/或 planner（需要素材才能写）
   - consolidator 依赖 coder（需要初稿才能整合）
   - citation 依赖 coder 或 consolidator（需要内容才能处理引用）
   - reviewer 依赖 coder/consolidator/citation（需要内容才能审校）
   - debugger 依赖 reviewer（需要审校报告才能修订）
3. 并行优化：无依赖的子智能体放同一批次并行（如 literature + planner 可并行）
4. 简单任务可只分配 1-2 个子智能体（如"生成目录"只需 coder）

必须输出 JSON 格式（不要任何其他内容）:
{"batches": [{"id": 1, "roles": ["角色名"], "depends_on": []}, ...]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户需求: {user_input}\n\n请生成执行计划。"},
        ]

        try:
            response = await self._llm_call(messages, max_tokens=500)
            content = _extract_json(response.content or "") or ""
            if not content:
                raise ValueError("执行计划未包含有效 JSON")
            plan = json.loads(content)
            # 规则校验并修正
            plan = self._validate_plan(plan)
            return plan
        except Exception as e:
            # LLM 生成失败 → 回退到经典流程（文献→方法→撰写→整理→引用→审校）
            logger.warning("执行计划生成失败，回退经典串行流程: %s", e)
            return self._fallback_plan()

    def _validate_plan(self, plan: dict) -> dict:
        """规则校验执行计划的依赖合理性，自动修正不合理的依赖

        校验分两层：
        1. 结构校验（方案A）：depends_on 必须引用更早批次，禁止自引用/环/不存在批次
        2. 域规则校验：关键角色必须出现在更早批次（按批次位置比较，非 id 数值）
        """
        # 域依赖规则：角色 -> 它依赖的角色（至少一个必须在更早的批次出现）
        dependency_rules = {
            "citation": ["coder", "consolidator"],      # 引用需要内容
            "reviewer": ["coder", "consolidator", "citation"],  # 审校需要内容
            "debugger": ["reviewer"],                    # 修订需要审校报告
            "consolidator": ["coder"],                   # 整理需要初稿
        }

        batches = plan.get("batches", [])
        if not batches:
            return self._fallback_plan()

        # 1) id 类型归一化：LLM 可能输出字符串 id（如 "1"）而 depends_on 用 int（如 1），
        #    类型不匹配会导致下方 depends_on 校验误判并整体回退全角色流水线。
        #    这里统一将 id / depends_on 转 int；id 无法解析时直接回退（无有效批次）。
        normalized_batches = []
        for batch in batches:
            try:
                bid = int(batch.get("id"))
            except (TypeError, ValueError):
                return self._fallback_plan()
            nb = dict(batch)
            nb["id"] = bid
            deps = []
            for d in (batch.get("depends_on") or []):
                try:
                    deps.append(int(d))
                except (TypeError, ValueError, AttributeError):
                    continue  # 无法解析的依赖忽略
            nb["depends_on"] = deps
            normalized_batches.append(nb)
        plan["batches"] = normalized_batches
        batches = normalized_batches

        # 2) 收集所有已分配角色（去重，重复的只保留第一个）
        seen_roles = set()
        cleaned_batches = []
        for batch in batches:
            roles = batch.get("roles", [])
            unique_roles = [r for r in roles if r not in seen_roles]
            if unique_roles:
                seen_roles.update(unique_roles)
                batch["roles"] = unique_roles
                cleaned_batches.append(batch)
        batches = cleaned_batches

        # 2) 结构校验：批次 id 必须唯一且非空
        batch_positions: dict = {}
        for i, b in enumerate(batches):
            bid = b.get("id")
            if bid is None or bid in batch_positions:
                return self._fallback_plan()
            batch_positions[bid] = i
        # depends_on 必须引用更早批次（杜绝自引用/环/引用不存在批次）
        for i, b in enumerate(batches):
            for dep_id in b.get("depends_on", []):
                if dep_id not in batch_positions or batch_positions[dep_id] >= i:
                    return self._fallback_plan()

        # 3) 域规则校验：关键角色必须出现在更早批次（按位置比较）
        role_position: dict = {}
        for i, b in enumerate(batches):
            for role in b.get("roles", []):
                role_position[role] = i

        for role, deps in dependency_rules.items():
            if role not in role_position:
                continue
            dep_satisfied = any(
                d in role_position and role_position[d] < role_position[role]
                for d in deps
            )
            # 特殊：coder 没有硬依赖（可基于用户需求直接写）
            if not dep_satisfied and role != "coder":
                return self._fallback_plan()

        plan["batches"] = batches
        return plan

    def _fallback_plan(self) -> dict:
        """回退计划：经典串行流程（文献→方法→撰写→整理→引用→审校）"""
        return {
            "batches": [
                {"id": 1, "roles": ["literature", "planner"], "depends_on": []},
                {"id": 2, "roles": ["coder"], "depends_on": [1]},
                {"id": 3, "roles": ["consolidator"], "depends_on": [2]},
                {"id": 4, "roles": ["citation"], "depends_on": [3]},
                {"id": 5, "roles": ["reviewer"], "depends_on": [4]},
            ]
        }

    async def _generate_outline(self, user_input: str, paper_type: str = "research") -> list[dict]:
        """主编生成论文大纲（按论文类型采用相应结构 + 各节字数预算）

        LLM 失败或解析失败时回退到该类型的默认大纲，保证流程不中断。
        """
        fallback = get_outline_for_type(paper_type)
        type_label = _PAPER_TYPE_LABELS.get(paper_type, "研究论文")
        system_prompt = f"""你是论文写作主编。根据用户需求生成{type_label}的大纲。

要求:
1. 采用{type_label}的标准章节结构，可根据具体方向增删章节
2. 每个章节给出: key（英文稳定标识）、title（中文标题）、target_words（目标字数）
3. 总字数控制在 8000-15000 字（期刊论文典型篇幅）

必须输出 JSON（不要其他内容）:
{{"sections": [{{"key": "introduction", "title": "引言", "target_words": 800}}, ...]}}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户需求: {user_input}\n\n请生成论文大纲。"},
        ]
        try:
            response = await self._llm_call(messages, max_tokens=800)
            return _parse_outline_response(response.content or "", fallback)
        except Exception as e:
            logger.warning("大纲生成失败，回退 %s 默认大纲: %s", paper_type, e)
            return list(fallback)

    async def _revise_round(self, report) -> str:
        """让修订员（debugger）根据质量门报告修订当前草稿，返回【修订章节】而非整篇。

        读取共享草稿全文 + 质量门待改进项（报告已按章节定位问题），
        只产出被修改的章节（保留 '## 章节标题' 结构）；未修改章节不输出，
        主流程据此定点覆盖，避免整篇重建导致的 token 浪费。
        """
        worker = self._get_worker_by_role_name("debugger")
        if worker is None:
            return ""
        draft = self.project.read_draft()
        prompt = (
            f"## 当前论文草稿\n{draft}\n\n"
            f"## 质量门待改进项（已定位到对应章节）\n{report.to_text()}\n\n"
            "请逐项修订上述待改进项：\n"
            "1. 只输出【需要修改的章节】，每个章节以 '## 章节标题' 开头；\n"
            "2. 未需要修改的章节一律不要输出（会被保留原样）；\n"
            "3. 若某问题需要新建章节，则新增对应的 '## 章节标题' 及内容。"
        )
        results = []
        async for event in worker.run(prompt):
            if event.type == "text":
                results.append(event.content)
        return "\n".join(results)

    async def _review_round(self) -> str:
        """LLM 审校核查员软复核：对当前全文草稿做一次验证，返回审校报告。

        用于修订之后的"二次复核"，与计划中的首次审校互补。
        """
        worker = self._get_worker_by_role_name("reviewer")
        if worker is None:
            return ""
        draft = self.project.read_draft()
        prompt = (
            f"## 当前论文草稿（修订后）\n{draft}\n\n"
            "请对上述修订后的论文执行软复核（多重验证），输出审校报告：\n"
            "1) 验证通过项 2) 仍存在问题项（位置+建议） 3) 存疑待确认项 4) 总体质量评估。"
        )
        results = []
        async for event in worker.run(prompt):
            if event.type == "text":
                results.append(event.content)
        return "\n".join(results)

    async def _generate_data_suggestions(self) -> str:
        """扫描【数据】占位，生成各位置的数据处理与来源建议报告。

        无占位时直接返回空串；调用 LLM 失败时降级为友好提示。
        """
        from sage.paper_data import find_data_placeholders, format_placeholders
        draft = self.project.read_draft()
        placeholders = find_data_placeholders(draft)
        if not placeholders:
            return ""

        system_prompt = (
            "你是科研数据顾问。论文正文中用【数据】标记了需要真实数据的位置。"
            "请针对每个位置，按编号给出：1) 需要什么数据 2) 数据处理/分析方法 "
            "3) 数据来源建议（公开数据集/实验采集/问卷/权威统计等）。分条输出。"
        )
        user_content = (
            f"论文草稿如下：\n{draft}\n\n"
            f"【数据】占位位置共 {len(placeholders)} 处：\n"
            f"{format_placeholders(placeholders)}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            response = await self._llm_call(messages, max_tokens=1500)
            return (response.content or "").strip()
        except Exception as e:
            logger.warning("数据建议生成失败: %s", e)
            return "（数据建议生成失败，请人工确认【数据】占位处所需数据。）"

    async def _run_parallel_workers(
        self,
        roles: list[str],
        user_input: str,
        batch_results: dict,
        project: Optional[PaperProject] = None,
        dep_roles: Optional[list[str]] = None,
    ) -> AsyncIterator[CollaborationEvent]:
        """并行运行多个 worker，实时 yield 各 worker 的事件

        Args:
            roles: 本批次要并行执行的角色列表
            user_input: 用户原始需求
            batch_results: 前序批次的产出 {role: result_text}
            project: 共享草稿文档（P0），worker 产出写入、下游读全文
            dep_roles: 本批次依赖的前序角色列表（由计划 depends_on 推导，方案A）

        Yields:
            CollaborationEvent: 各 worker 产生的事件（边产边转，实时交错的真流式）
        """
        dep_roles = list(dep_roles or [])
        # 共享事件队列：worker 每产出一个事件立即入队，主循环按 FIFO 逐一转发；
        # worker 完成信号 (role_name, text_output, ok) 也走同一队列，
        # 保证「worker_start → 过程事件 → 完成信号」的相对顺序不丢（单队列无竞态）
        event_queue: asyncio.Queue = asyncio.Queue()

        async def run_single(role_name: str):
            """运行单个 worker，事件边产边入队（不再等完整结束才 yield）"""
            # 依赖上下文由 LLM 的 depends_on 决定（方案A），传递完整产出
            dep_context = _build_dependency_context(dep_roles, batch_results)
            prompt = _build_worker_prompt(
                user_input=user_input,
                role_name=role_name,
                dep_context=dep_context,
                project=project,
            )

            role_label = _ROLE_LABELS.get(role_name, role_name)
            text_parts: list[str] = []
            ok = False
            try:
                # 获取 worker 并运行
                worker = self._get_worker_by_role_name(role_name)
                if worker is None:
                    await event_queue.put(CollaborationEvent(
                        type="worker_done",
                        role=role_name,
                        content=f"{role_label}角色不存在，跳过",
                    ))
                    return

                await event_queue.put(CollaborationEvent(
                    type="worker_start",
                    role=role_name,
                    content=f"{role_label}开始工作...",
                ))

                async for event in worker.run(prompt):
                    mapped = self._map_event(event, role_name)
                    if mapped:
                        # 实时转发给前端（不等 worker 结束）
                        await event_queue.put(mapped)
                        if mapped.type == "text":
                            text_parts.append(mapped.content)
                ok = True  # 正常产出完毕
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("worker %s 异常: %s", role_name, e)
                await event_queue.put(CollaborationEvent(
                    type="worker_done",
                    role=role_name,
                    content=f"{role_label}异常终止: {e}",
                ))
            finally:
                # 完成信号：跑完（无论成败）都会入队，供主循环收尾
                await event_queue.put((role_name, _join_stream_text(text_parts), ok))

        # 并行启动所有 worker
        tasks = [asyncio.create_task(run_single(r)) for r in roles]
        finished = 0
        try:
            # 主循环：单队列 FIFO 逐项消费（事件与完成信号统一处理），
            # worker 极快完成时也不会出现事件被跳过/顺序错乱
            while finished < len(tasks):
                item = await event_queue.get()
                if isinstance(item, CollaborationEvent):
                    yield item
                    continue
                role_name, text_output, ok = item
                finished += 1
                if ok:
                    # 正常完成补发汇总事件（异常/缺角色分支已由 run_single 入队，不重复）
                    yield CollaborationEvent(
                        type="worker_done",
                        role=role_name,
                        content=f"{_ROLE_LABELS.get(role_name, role_name)}完成",
                    )
                # 存储完整结果供后续批次使用
                batch_results[role_name] = text_output
                # 写入共享草稿：素材全文 + 正文分节结构化（供下游读全文）
                if project is not None and text_output:
                    project.store_material(role_name, text_output)
                    if role_name in ("coder", "consolidator", "debugger"):
                        project.parse_draft_to_sections(text_output)
                    # 引用管理员完成后：落盘「引用说明对照清单」为 citations.md
                    if role_name == "citation":
                        self._persist_citation_manifest(text_output, project)
                    # 边写边落盘：任一 agent 产生正文即持久化结构化状态，
                    # 使「成稿审阅」实时可见过程版本，而非等全部生成完才出现。
                    project.save()
        except asyncio.CancelledError:
            # 客户端断开：取消所有仍在运行的 worker，避免后台残留任务
            for t in tasks:
                if not t.done():
                    t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            raise
        except Exception:
            # 主循环异常：取消剩余 worker，避免任务悬挂
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

    def _has_file_changes(self, agent: AgentLoop) -> bool:
        """检查 Agent 是否进行了实际文件修改（Sage 不依赖 git）"""
        # Sage 论文写作系统不使用 git 检测文件变更，
        # 简化实现：只要 Agent 调用了 write_file/edit_file 工具即视为有修改
        return True

    def _persist_citation_manifest(self, citation_output: str, project: PaperProject):
        """引用管理员产出中包含的「引用说明对照清单」落盘为 workspace/citations.md

        从引用管理员输出中抽取以 "# 引用说明对照清单" 开头的（代码块）内容，
        写入工作空间根目录 citations.md，便于用户逐处核对引用来源与理由。
        抽取失败时给出提示性文件，不中断流程。
        """
        try:
            output = citation_output or ""
            manifest = ""
            # 优先取代码块中的完整清单
            for block in re.findall(r"```(?:markdown|md)?\s*(.*?)```", output, re.DOTALL):
                if "引用" in block and ("文献" in block or "理由" in block):
                    manifest = block.strip()
                    break
            if not manifest and "# 引用说明对照清单" in output:
                start = output.index("# 引用说明对照清单")
                manifest = output[start:].strip()
            # 无显现成文的清单时，降级截取产出核心部分
            if not manifest:
                manifest = (
                    "# 引用说明对照清单\n\n"
                    "（引用管理员未输出结构化清单，以下为编辑处理原始产出节选，"
                    "建议人工核对正文引用是否正确插入）\n\n" + output[:2000]
                )
            # 落盘位置随草稿隔离：对话草稿目录或工作区根目录（保持与 paper.md 同目录对照）
            citations_path = self.project._draft_path.parent / "citations.md"
            citations_path.write_text(manifest.rstrip() + "\n", encoding="utf-8")
            logger.info("引用说明对照清单已落盘: %s", citations_path)
        except Exception as e:
            logger.warning("引用说明对照清单落盘失败: %s", e)

    def _map_event(self, event: LoopEvent, role: str) -> Optional[CollaborationEvent]:
        """将 AgentLoop 事件映射为 CollaborationEvent"""
        # reasoning 事件直接透传（模型思考内容）
        if event.type == "reasoning":
            return CollaborationEvent(
                type="reasoning",
                role=role,
                content=event.content,
            )
        # text 事件直接透传
        if event.type == "text":
            return CollaborationEvent(
                type="text",
                role=role,
                content=event.content,
            )
        # retry 事件直接透传（LLM 调用重试通知）
        if event.type == "retry":
            return CollaborationEvent(
                type="retry",
                role=role,
                content=event.content,
                metadata=event.tool_args or {},
            )
        # progress 事件直接透传（长任务进度通知）
        if event.type == "progress":
            return CollaborationEvent(
                type="progress",
                role=role,
                content=event.content,
                metadata=event.tool_args or {},
            )
        # context_usage 事件直接透传（上下文用量指示）
        if event.type == "context_usage":
            return CollaborationEvent(
                type="context_usage",
                role=role,
                content=event.content,
                metadata=event.tool_args or {},
            )
        # 工具事件透传（统一链路：前端展示工具调用与结果，与单Agent路径一致）
        if event.type == "tool_start":
            return CollaborationEvent(
                type="tool_start",
                role=role,
                content=event.content,
                metadata={"tool": event.tool_name, "args": event.tool_args},
                tokens=event.tokens or {},
            )
        if event.type == "tool_result":
            return CollaborationEvent(
                type="tool_result",
                role=role,
                content=event.content,
                metadata={"tool": event.tool_name, "args": event.tool_args},
            )
        mapping = {
            "error": "worker_done",
        }
        mapped_type = mapping.get(event.type, "worker_start")
        return CollaborationEvent(
            type=mapped_type,
            role=role,
            content=event.content,
            metadata={"tool": event.tool_name, "args": event.tool_args} if event.tool_name else {},
            tokens=event.tokens or {},
        )


def create_orchestrator(workspace: Optional[Path] = None, conversation_id: Optional[str] = None, pool_mode: bool = False) -> AgentOrchestrator:
    """创建多 Agent 编排器（工厂函数）

    conversation_id 用于按对话隔离论文草稿（不同对话各自一篇论文，
    存储在 .sage/papers/{conversation_id}/ 下）。
    pool_mode 为 True 时 worker 的文献检索覆盖所有工作空间。
    """
    return AgentOrchestrator(workspace=workspace, conversation_id=conversation_id, pool_mode=pool_mode)


# ── 协作上下文构建（P0 修复：全文传递，替换旧的 [:2000] 截断）──

_ROLE_PROMPTS: dict[str, str] = {
    "literature": "请针对以下论文写作需求进行文献调研。输出格式: 1) 研究背景与发展脉络 2) 主要研究流派 3) 研究空白与机会 4) 关键参考文献列表（含DOI/URL）",
    "planner": "请基于以下材料设计研究方法。输出格式: 1) 研究问题与假设 2) 研究方法选型与理由 3) 实验/研究设计 4) 数据分析方法 5) 论证框架",
    "coder": "请基于以下材料撰写论文内容。要求：1) 结构完整 2) 需要引用处用 [CITE: 关键词] 标注 3) 学术语言规范 4) 涉及实验数据/统计结果/具体数值处，一律用【数据】占位，严禁编造具体数据",
    "consolidator": "请整合以下产出，消除重复、调和矛盾、统一风格，输出连贯完整的论文内容。",
    "citation": "请处理所有 [CITE: 关键词] 标记：1) 从文献库匹配相关文献 2) 插入规范引用 3) 格式化参考文献列表 4) 验证引用真实性 5) 标注存疑引用。\n处理完成后，额外输出一份「引用说明对照清单」（markdown，用代码块包裹），供用户逐处核对。格式要求：\n# 引用说明对照清单\n\n每一处引用一条记录，包含 4 个字段：\n- 位置：正文中该引用的章节/段落位置\n- 原文摘录：引用处的原文片段（≤60字）\n- 对应文献：文献标题 + 作者/年份 + DOI/URL（引用来源须为用户工作空间内的参考文献或联网检索到的真实文献）\n- 引用理由：为什么此处引用这篇文献（须用一句话说明该文献与上下文论证的关系）\n\n引用真正插入正文后，[CITE: 关键词] 标记必须被替换为规范引用标注（如 [1][2] 或作者年份），不得残留。",
    "reviewer": "请执行四重验证: 1) 文献库验证 2) 逻辑核查 3) 外部检索验证（存疑引用）4) 学术规范检查。输出审校报告。",
    "debugger": "请根据审校报告修订论文，处理存疑引用、修复逻辑问题、调整格式。",
}

# 需要把论文大纲注入 prompt 的角色（它们直接产出/加工论文正文）
_OUTLINE_AWARE_ROLES = {"coder", "consolidator", "citation", "reviewer", "debugger"}

# 二次复核闭环：质量门发现"可修复"问题时，让修订员修订后重查，上限轮数
_REVISION_MAX_ROUNDS = 2
# 可由修订员直接修复的问题类型（过短/缺参考文献不在此列，交由人工或引用管理员）
_REVISION_ACTIONABLE_CODES = {"cite_marker_left", "section_missing"}

# 断点续写触发词：命中且存在已有草稿时，续写未完成章节
_CONTINUATION_KEYWORDS = ("继续写", "续写", "接着写", "接着", "往下写", "补充", "完善", "继续")

# 论文局部写作："论文的摘要/目录/引言/第N章..." → 局部撰写，而非完整论文
_PARTIAL_OF_PAPER_RE = re.compile(
    r"(论文|文章|paper|报告)\s*的?\s*"
    r"(摘要|目录|引言|绪论|前言|结论|大纲|正文|标题|第[一二三四五六七八九十\d]+章)"
)

# 论文类型中文标签（大纲 prompt 提示用）
_PAPER_TYPE_LABELS = {
    "review": "综述论文",
    "empirical": "实证研究论文",
    "theoretical": "理论研究论文",
    "case": "案例研究论文",
    "research": "研究论文",
}


def _is_continuation(text: str) -> bool:
    """判断用户意图是否为"续写/完善已有草稿"。"""
    return any(k in text for k in _CONTINUATION_KEYWORDS)


def _detect_paper_type(text: str) -> str:
    """识别论文类型：review/empirical/theoretical/case，默认 research。"""
    lower = text.lower()
    if any(k in lower for k in ("综述", "survey", "review paper", "文献综述")):
        return "review"
    if any(k in lower for k in ("实证", "empirical", "量化", "实验研究")):
        return "empirical"
    if any(k in lower for k in ("理论", "theoretical", "模型推导", "纯理论")):
        return "theoretical"
    if any(k in lower for k in ("案例", "case study", "案例分析")):
        return "case"
    return "research"


def _needs_clarification(text: str, has_draft: bool = False) -> bool:
    """信息严重不足时返回 True，触发澄清回路。

    保守策略：仅在明显模糊（极短、或"写论文"这类无主题的裸请求）时触发，
    避免打断正常请求。
    """
    if has_draft:
        return False
    t = (text or "").strip()
    if not t:
        return True
    # 统一链路：日常问候/闲聊等短消息交给意图分析路由，仅含写作意图的短消息触发澄清
    if len(t) < 4 and re.search(r"写|论文|paper|文章|生成|撰写", t, re.IGNORECASE):
        return True
    # 裸请求："写论文" / "帮我写论文" / "写一篇论文" / "生成文章" 等无主题词
    if re.fullmatch(r"(帮我|请|想|要|麻烦)?\s*(写|生成|撰写)\s*(一篇|一个|个)?\s*(论文|文章|paper)?", t):
        return True
    return False


def _join_stream_text(parts: list[str]) -> str:
    """把 LLM 流式输出的 text 碎片拼接为连贯正文。

    逐个 token 的流式碎片若用换行拼接会得到"每词一行"的竖排脏文本。
    这里规则：
      - 保留碎片内部自带的换行；
      - 碎片边界处，仅当相邻都是 ASCII 字母/数字时补一个空格（防 "The"+"user"→"Theuser"），
        中文与标点边界不增加空白，直接相接。
    """
    out = ""
    for p in parts:
        if not p:
            continue
        if out:
            prev = out[-1]
            cur = p[0]
            if (
                prev.isascii() and prev.isalnum()
                and cur.isascii() and cur.isalnum()
            ):
                out += " "
        out += p
    return out


def _fit_context(text: str, max_tokens: int) -> str:
    """按 token 预算约束上下文。预算内原样返回；超预算保留头尾、省略中间。"""
    if not text:
        return ""
    try:
        from sage.context.tokenizer import count_tokens
        if count_tokens(text) <= max_tokens:
            return text
    except Exception:
        return text
    head_chars = int(len(text) * 0.7)
    tail_chars = int(len(text) * 0.2)
    return (
        text[:head_chars]
        + f"\n\n[上下文过长，已省略中间 {len(text) - head_chars - tail_chars} 字符]\n\n"
        + text[-tail_chars:]
    )


def _build_dependency_context(dep_roles: list[str], batch_results: dict, max_tokens: int = 45000) -> str:
    """构建下游 worker 的依赖上下文 — 传递【dep_roles 指定角色】的完整产出。

    依赖角色由 LLM 生成计划的 depends_on 推导（方案 A：depends_on 真正驱动上下文），
    不再使用硬编码角色依赖表。仅当总 token 数超过 max_tokens 时才按预算保留头尾。
    """
    parts = []
    for dep in dep_roles:
        content = batch_results.get(dep) or ""
        if content:
            parts.append(f"## {dep} 的完整产出\n{content}")
    if not parts:
        return ""
    return _fit_context("\n\n".join(parts), max_tokens)


def _build_worker_prompt(
    user_input: str,
    role_name: str,
    dep_context: str = "",
    project: Optional[PaperProject] = None,
) -> str:
    """组装 worker prompt：用户需求 + 依赖全文 + 大纲（大纲感知角色）+ 角色引导。"""
    parts = [f"## 用户需求\n{user_input}"]
    if dep_context:
        parts.append(dep_context)
    if project is not None and role_name in _OUTLINE_AWARE_ROLES:
        outline = project.outline_text()
        if outline:
            parts.append(outline)
            if role_name == "coder":
                parts.append(
                    "撰写要求：严格按上述大纲逐节撰写，每节以 '## 章节标题' 开头，"
                    "遵守各节目标字数预算。"
                )
    role_prompt = _ROLE_PROMPTS.get(role_name, "")
    if role_prompt:
        parts.append(role_prompt)
    return "\n\n".join(parts)


def _build_simple_prompt(role_name: str, user_input: str, project: Optional[PaperProject] = None) -> str:
    """组装简单任务 prompt：对撰写/修订类角色注入当前草稿全文（若存在）。

    让简单任务（如"写结论"/"润色这段"）能看到已有草稿，与复杂任务的共享草稿打通。
    """
    draft = project.read_draft() if project is not None else ""
    if role_name in ("coder", "debugger") and draft.strip():
        return (
            f"## 当前论文草稿\n{draft}\n\n"
            f"## 用户请求\n{user_input}\n\n"
            "请基于草稿处理上述请求（撰写缺失内容或按需修改），"
            "输出 markdown（保留 '## 章节标题' 结构）。"
        )
    return user_input


def _extract_json(content: str) -> Optional[str]:
    """从 LLM 输出中提取 JSON 文本（容错处理）

    覆盖三种常见噪声：
    1. Markdown 代码块包裹：```json { ... } ```
    2. 前后缀说明文字：XX 结果如下 { ... } XX
    3. 内容里出现多个对象时优先取第一个完整 JSON 对象（贪婪匹配支持嵌套）

    提取失败返回 None。本函数统一替代各环节散落的 JSON 提取逻辑。
    """
    text = (content or "").strip()
    if not text:
        return None
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1)
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, text, re.DOTALL)
        if m:
            return m.group(0)
    return None


def _parse_outline_response(content: str, fallback: Optional[list[dict]] = None) -> list[dict]:
    """解析大纲 LLM 响应为 sections 列表；失败返回 fallback（默认 IMRaD）。"""
    fb = fallback or DEFAULT_OUTLINE
    text = _extract_json(content)
    if not text:
        return list(fb)
    try:
        data = json.loads(text)
        sections = data.get("sections") or data.get("outline") or []
        if isinstance(sections, list) and sections:
            normalized = []
            for s in sections:
                if isinstance(s, dict) and (s.get("key") or s.get("title")):
                    normalized.append({
                        "key": str(s.get("key", "")),
                        "title": str(s.get("title", "")),
                        "target_words": int(s.get("target_words", 0) or 0),
                    })
            if normalized:
                return normalized
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return list(fb)
